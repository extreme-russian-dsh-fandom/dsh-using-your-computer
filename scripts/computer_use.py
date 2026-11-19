#!/usr/bin/env python3
"""
DeepSeek Computer Use — Windows screen / mouse / keyboard control engine.

Gives an LLM agent direct control of the user's input devices the way Codex's
"using your computer" does, with four hard guarantees baked in:

  1.  Screen highlighting.  Taking control draws pleasant ORANGE gradient edges
      (fading inward) plus a liquid-glass (acrylic-blurred) banner around the
      desktop; every click / drag / move target draws a transient ORANGE box at
      the exact point that will be clicked, so the user always sees what the
      agent is about to touch.
  2.  Guaranteed return of control.  A global cancel key aborts the session
      from anywhere, any time, even if the agent crashes. Default is ESC; it is
      configurable in DSH (see "Cancel key" below), per call with
      'begin --cancel-key', or with COMPUTER_USE_CANCEL_KEY.
      The overlay windows are owned by a daemon process: if that process dies
      for any reason, Windows destroys its windows automatically, so a stuck
      highlight is structurally impossible. Every action re-checks the stop
      flag before touching the device.
  3.  Minimal inter-event delay.  Cursor moves are instantaneous (SetCursorPos);
      clicks, key presses, and typed characters use ~10 ms between events — no
      100 ms / 250 ms / 1 s pauses anywhere. One tunable knob: EVENT_DELAY_MS.
  4.  Correct coordinate grounding.  The process is made DPI-aware
      (PER-MONITOR-AWARE-V2) before anything else, so screenshots, screen
      metrics, and cursor coordinates all live in the SAME physical-pixel
      space. This is the single most common failure in LLM computer-use
      (see openai/codex issue #50620): a non-DPI-aware process reads a scaled
      screenshot but injects physical pixels, so every click lands off-target.

Dependencies: Python stdlib (ctypes, subprocess, json, argparse, ...) and
Pillow (for screenshots). Nothing else. No pyautogui, no pywinauto.

Usage (all subcommands are self-contained processes):

    python computer_use.py begin [--cancel-key ESC]  # acquire control: island + cancel key
    python computer_use.py screenshot [--out s.png]
    python computer_use.py cursor_position
    python computer_use.py move 800 450
    python computer_use.py click 800 450 [--button left|right|middle] [--clicks 1|2]
    python computer_use.py drag 100 100 400 400
    python computer_use.py scroll 800 450 3      # +up / -down
    python computer_use.py type --text "hello"   # or --file path
    python computer_use.py key "ctrl+c"          # combos or names: enter, tab, esc...
    python computer_use.py highlight 800 450     # preview target box
    python computer_use.py clear_highlight
    python computer_use.py status
    python computer_use.py config [--cancel-key "Ctrl+Alt+Esc"] [--reset]  # DSH setting
    python computer_use.py self_test             # non-destructive diagnostics
    python computer_use.py end                   # release control, clear overlays
"""

import argparse
import ctypes
import json
import os
import random
import subprocess
import sys
import tempfile
import threading
import time

# ----------------------------------------------------------------------------
# DPI awareness — MUST run before any screen metric is read or any coordinate
# is injected. Makes screenshots, GetSystemMetrics, and SetCursorPos all agree
# in physical pixels. Fall back gracefully across Windows versions.
# ----------------------------------------------------------------------------

def set_dpi_awareness():
    try:
        # PER_MONITOR_AWARE_V2 = -4
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PROCESS_PER_MONITOR_DPI_AWARE
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


set_dpi_awareness()

# ----------------------------------------------------------------------------
# Win32 handles, constants, and typed prototypes (argtypes/restype are set so
# 64-bit handles are never truncated and wide strings convert correctly).
# ----------------------------------------------------------------------------

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

# --- Input injection ---
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x01000
MOUSEEVENTF_ABSOLUTE = 0x8000

KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

# --- Window / overlay ---
WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

LWA_ALPHA = 0x00000002
SW_SHOWNOACTIVATE = 4

ULW_ALPHA = 0x00000002          # UpdateLayeredWindow: per-pixel alpha
AC_SRC_ALPHA = 0x01             # BLENDFUNCTION AlphaFormat: use source alpha
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
WCA_ACCENT_POLICY = 19

WM_PAINT = 0x000F
WM_DESTROY = 0x0002
WM_TIMER = 0x0113
WM_QUIT = 0x0012
PM_REMOVE = 0x0001

# --- Cancel key: low-level keyboard hook ---
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_KEYUP = 0x0101
WM_SYSKEYDOWN = 0x0104
WM_SYSKEYUP = 0x0105
LLKHF_INJECTED = 0x10          # synthetic input (SendInput) — never a real keypress

# Modifier bits, same values RegisterHotKey documents; only used as our own
# bitmask (the hook path checks physical state instead of registering a hotkey).
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

VK_SHIFT_ = 0x10
VK_CONTROL_ = 0x11
VK_ALT_ = 0x12
VK_LWIN_ = 0x5B
VK_RWIN_ = 0x5C
KEY_PRESSED = 0x8000           # GetAsyncKeyState high bit: key is down

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000

PROCESS_QUERY_INFORMATION = 0x0400
SYNCHRONIZE = 0x00100000
STILL_ACTIVE = 259

# --- Typed prototypes (prevents 64-bit handle truncation + string issues) ---
_ULONG_PTR = ctypes.c_size_t

# ctypes.wintypes is incomplete and varies across Python versions, so define
# exactly the Windows types we need, from ctypes primitives.
BOOL = ctypes.c_int          # Win32 BOOL is a 32-bit int
BYTE = ctypes.c_ubyte
WORD = ctypes.c_uint16
DWORD = ctypes.c_uint32
UINT = ctypes.c_uint
LONG = ctypes.c_long
SHORT = ctypes.c_short
WCHAR = ctypes.c_wchar
COLORREF = ctypes.c_uint32
ATOM = ctypes.c_uint16
LRESULT = ctypes.c_ssize_t
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
HANDLE = ctypes.c_void_p
HWND = ctypes.c_void_p
HDC = ctypes.c_void_p
HINSTANCE = ctypes.c_void_p
HMODULE = ctypes.c_void_p
HBRUSH = ctypes.c_void_p
HFONT = ctypes.c_void_p
HGDIOBJ = ctypes.c_void_p
HICON = ctypes.c_void_p
HCURSOR = ctypes.c_void_p
HMENU = ctypes.c_void_p
LPVOID = ctypes.c_void_p
LPCWSTR = ctypes.c_wchar_p


class POINT(ctypes.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class RECT(ctypes.Structure):
    _fields_ = [("left", LONG), ("top", LONG), ("right", LONG), ("bottom", LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", HWND),
        ("message", UINT),
        ("wParam", WPARAM),
        ("lParam", LPARAM),
        ("time", DWORD),
        ("pt", POINT),
        ("lPrivate", DWORD),  # present in the real MSG; must match its size
    ]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)
HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, WPARAM, LPARAM)


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", DWORD),
        ("scanCode", DWORD),
        ("flags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", HINSTANCE),
        ("hIcon", HICON),
        ("hCursor", HCURSOR),
        ("hbrBackground", HBRUSH),
        ("lpszMenuName", LPCWSTR),
        ("lpszClassName", LPCWSTR),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", DWORD),
        ("biWidth", LONG),
        ("biHeight", LONG),
        ("biPlanes", WORD),
        ("biBitCount", WORD),
        ("biCompression", DWORD),
        ("biSizeImage", DWORD),
        ("biXPelsPerMeter", LONG),
        ("biYPelsPerMeter", LONG),
        ("biClrUsed", DWORD),
        ("biClrImportant", DWORD),
    ]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [
        ("BlendOp", BYTE),
        ("BlendFlags", BYTE),
        ("SourceConstantAlpha", BYTE),
        ("AlphaFormat", BYTE),
    ]


class SIZE(ctypes.Structure):
    _fields_ = [("cx", LONG), ("cy", LONG)]


class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [
        ("AccentState", ctypes.c_int),
        ("AccentFlags", ctypes.c_int),
        ("GradientColor", ctypes.c_int),   # ABGR: alpha in the high byte
        ("AnimationId", ctypes.c_int),
    ]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [
        ("Attrib", ctypes.c_int),
        ("pvData", ctypes.c_void_p),
        ("cbData", ctypes.c_size_t),
    ]

user32.SetProcessDpiAwarenessContext.restype = BOOL
user32.SetProcessDpiAwarenessContext.argtypes = [HANDLE]
user32.SetProcessDPIAware.restype = BOOL

user32.GetSystemMetrics.restype = ctypes.c_int
user32.GetSystemMetrics.argtypes = [ctypes.c_int]

user32.SetCursorPos.restype = BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.GetCursorPos.restype = BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]

user32.SendInput.restype = UINT
user32.SendInput.argtypes = [UINT, ctypes.c_void_p, ctypes.c_int]

user32.VkKeyScanW.restype = ctypes.c_short
user32.VkKeyScanW.argtypes = [WCHAR]

user32.RegisterClassW.restype = ATOM
user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASS)]

kernel32.GetModuleHandleW.restype = HMODULE
kernel32.GetModuleHandleW.argtypes = [LPCWSTR]

gdi32.CreateSolidBrush.restype = HBRUSH
gdi32.CreateSolidBrush.argtypes = [COLORREF]

user32.CreateWindowExW.restype = HWND
user32.CreateWindowExW.argtypes = [
    DWORD, LPCWSTR, LPCWSTR, DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    HWND, HMENU, HINSTANCE, LPVOID,
]

user32.SetLayeredWindowAttributes.restype = BOOL
user32.SetLayeredWindowAttributes.argtypes = [
    HWND, COLORREF, BYTE, DWORD,
]

user32.ShowWindow.restype = BOOL
user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
user32.UpdateWindow.restype = BOOL
user32.UpdateWindow.argtypes = [HWND]
user32.DestroyWindow.restype = BOOL
user32.DestroyWindow.argtypes = [HWND]

user32.GetClientRect.restype = BOOL
user32.GetClientRect.argtypes = [HWND, ctypes.POINTER(RECT)]

gdi32.SetBkMode.restype = ctypes.c_int
gdi32.SetBkMode.argtypes = [HDC, ctypes.c_int]
gdi32.SetTextColor.restype = COLORREF
gdi32.SetTextColor.argtypes = [HDC, COLORREF]

gdi32.CreateFontW.restype = HFONT
gdi32.CreateFontW.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    DWORD, DWORD, DWORD, DWORD, DWORD,
    DWORD, DWORD, DWORD, LPCWSTR,
]
gdi32.SelectObject.restype = HGDIOBJ
gdi32.SelectObject.argtypes = [HDC, HGDIOBJ]
gdi32.DeleteObject.restype = BOOL
gdi32.DeleteObject.argtypes = [HGDIOBJ]

user32.DrawTextW.restype = ctypes.c_int
user32.DrawTextW.argtypes = [
    HDC, LPCWSTR, ctypes.c_int, ctypes.POINTER(RECT), UINT,
]
gdi32.GetTextExtentPoint32W.restype = BOOL
gdi32.GetTextExtentPoint32W.argtypes = [HDC, LPCWSTR, ctypes.c_int, ctypes.POINTER(SIZE)]

user32.BeginPaint.restype = HDC
user32.BeginPaint.argtypes = [HWND, ctypes.c_void_p]
user32.EndPaint.restype = BOOL
user32.EndPaint.argtypes = [HWND, ctypes.c_void_p]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [HWND, UINT, WPARAM, LPARAM]
user32.PostQuitMessage.restype = None
user32.PostQuitMessage.argtypes = [ctypes.c_int]

user32.SetWindowsHookExW.restype = HANDLE
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, HINSTANCE, DWORD]
user32.UnhookWindowsHookEx.restype = BOOL
user32.UnhookWindowsHookEx.argtypes = [HANDLE]
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [HANDLE, ctypes.c_int, WPARAM, LPARAM]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]

user32.PeekMessageW.restype = BOOL
user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), HWND, UINT, UINT, UINT]
user32.TranslateMessage.restype = BOOL
user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
user32.DispatchMessageW.restype = LRESULT
user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]

kernel32.OpenProcess.restype = HANDLE
kernel32.OpenProcess.argtypes = [DWORD, BOOL, DWORD]
kernel32.GetExitCodeProcess.restype = BOOL
kernel32.GetExitCodeProcess.argtypes = [HANDLE, ctypes.POINTER(DWORD)]
kernel32.CloseHandle.restype = BOOL
kernel32.CloseHandle.argtypes = [HANDLE]

# Per-pixel-alpha gradient edges (UpdateLayeredWindow) + acrylic glass banner
gdi32.CreateDIBSection.restype = HANDLE
gdi32.CreateDIBSection.argtypes = [
    HDC, ctypes.c_void_p, UINT, ctypes.POINTER(ctypes.c_void_p), HANDLE, DWORD,
]
gdi32.CreateCompatibleDC.restype = HDC
gdi32.CreateCompatibleDC.argtypes = [HDC]
gdi32.DeleteDC.restype = BOOL
gdi32.DeleteDC.argtypes = [HDC]
gdi32.CreateRoundRectRgn.restype = HANDLE
gdi32.CreateRoundRectRgn.argtypes = [
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
]
user32.GetDC.restype = HDC
user32.GetDC.argtypes = [HWND]
user32.ReleaseDC.restype = ctypes.c_int
user32.ReleaseDC.argtypes = [HWND, HDC]
user32.UpdateLayeredWindow.restype = BOOL
user32.UpdateLayeredWindow.argtypes = [
    HWND, HDC, ctypes.POINTER(POINT), ctypes.POINTER(SIZE), HDC,
    ctypes.POINTER(POINT), COLORREF, ctypes.POINTER(BLENDFUNCTION), DWORD,
]
user32.SetWindowRgn.restype = ctypes.c_int
user32.SetWindowRgn.argtypes = [HWND, HANDLE, BOOL]
user32.SetWindowCompositionAttribute.restype = BOOL
user32.SetWindowCompositionAttribute.argtypes = [HWND, ctypes.c_void_p]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", LONG),
        ("dy", LONG),
        ("mouseData", DWORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", WORD),
        ("wScan", WORD),
        ("dwFlags", DWORD),
        ("time", DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _INPUT_UNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", DWORD), ("union", _INPUT_UNION)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [
        ("hdc", HDC),
        ("fErase", BOOL),
        ("rcPaint", RECT),
        ("fRestore", BOOL),
        ("fIncUpdate", BOOL),
        ("rgbReserved", ctypes.c_byte * 32),
    ]


# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

def _parse_color(hexstr):
    h = hexstr.strip().lstrip("#")
    return (int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


ORANGE = _parse_color(os.environ.get("COMPUTER_USE_COLOR", "FF8C1A"))  # warm orange
OVERLAY_ALPHA = int(os.environ.get("COMPUTER_USE_ALPHA", "210"))       # 0..255
EVENT_DELAY_MS = int(os.environ.get("COMPUTER_USE_DELAY_MS", "10"))    # inter-event
HIGHLIGHT_LINGER_MS = int(os.environ.get("COMPUTER_USE_LINGER_MS", "150"))
# Default cancel key. The effective key resolves in this order (first wins):
#   1. 'begin --cancel-key'            (this session only)
#   2. COMPUTER_USE_CANCEL_KEY         (environment, e.g. one agent/session)
#   3. ~/.dsh/computer-use.json        (the DSH setting: 'config --cancel-key')
#   4. DEFAULT_CANCEL_KEY
DEFAULT_CANCEL_KEY = "ESC"
BANNER_FONT_PX = int(os.environ.get("COMPUTER_USE_BANNER_FONT", "18"))     # legacy (unused)
# Island (top pill) design tokens — calm neutral surface + one orange accent.
ISLAND_HEIGHT = 44
ISLAND_FONT_PX = 15
ISLAND_BG = (18, 18, 20)          # #121214 near-black neutral
ISLAND_TEXT = (242, 242, 247)     # #F2F2F7 off-white (≈16:1 on ISLAND_BG)
ISLAND_HINT = (142, 142, 147)     # #8E8E93 secondary gray (≈5.7:1)
SESSION_DIR = os.path.join(tempfile.gettempdir(), "computer_use")
SESSION_FILE = os.path.join(SESSION_DIR, "session.json")

# The DSH-side setting lives with the rest of the DSH state, so it survives a
# skill update and a reboot. COMPUTER_USE_HOME overrides the ~/.dsh location;
# COMPUTER_USE_CONFIG overrides the whole file path (used by the tests).
CONFIG_FILENAME = "computer-use.json"


def _dsh_home():
    return os.environ.get("COMPUTER_USE_HOME") or os.path.join(
        os.path.expanduser("~"), ".dsh")


def config_path():
    return os.environ.get("COMPUTER_USE_CONFIG") or os.path.join(
        _dsh_home(), CONFIG_FILENAME)


def _read_config():
    path = config_path()
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_config(data):
    path = config_path()
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


# ----------------------------------------------------------------------------
# Cancel key spec  ("ESC", "Ctrl+Alt+Esc", "f8", ...)
# ----------------------------------------------------------------------------

# name -> (vk, label). The label is what the user reads, so it uses the same
# spelling Microsoft uses in its own shortcut docs (Esc, Enter, F8, ...).
_CANCEL_KEY_NAMES = {
    # Escape first: it is the default.
    "esc": (0x1B, "Esc"), "escape": (0x1B, "Esc"),
    "f1": (0x70, "F1"), "f2": (0x71, "F2"), "f3": (0x72, "F3"),
    "f4": (0x73, "F4"), "f5": (0x74, "F5"), "f6": (0x75, "F6"),
    "f7": (0x76, "F7"), "f8": (0x77, "F8"), "f9": (0x78, "F9"),
    "f10": (0x79, "F10"), "f11": (0x7A, "F11"), "f12": (0x7B, "F12"),
    "pause": (0x13, "Pause"), "break": (0x13, "Pause"),
    "scrolllock": (0x91, "ScrollLock"),
    "printscreen": (0x2C, "PrintScreen"), "prtsc": (0x2C, "PrintScreen"),
    "insert": (0x2D, "Insert"), "ins": (0x2D, "Insert"),
    "decimal": (0x6E, "Decimal"), "numdel": (0x6E, "Decimal"),
    # Space and modifiers alone are rejected below: both are unsafe as a
    # cancel key (Space belongs to the app, a lone modifier is a typo magnet).
    "backquote": (0xC0, "Backquote"), "grave": (0xC0, "Backquote"),
    "`": (0xC0, "Backquote"),
    "end": (0x23, "End"),
}

_CANCEL_KEY_MODS = {
    # alias -> (MOD_* bit, VK queried for physical state, canonical label)
    "ctrl": (MOD_CONTROL, VK_CONTROL_, "Ctrl"),
    "control": (MOD_CONTROL, VK_CONTROL_, "Ctrl"),
    "alt": (MOD_ALT, VK_ALT_, "Alt"),
    "shift": (MOD_SHIFT, VK_SHIFT_, "Shift"),
    "win": (MOD_WIN, VK_LWIN_, "Win"),
    "windows": (MOD_WIN, VK_LWIN_, "Win"),
    "meta": (MOD_WIN, VK_LWIN_, "Win"),
}

_CANCEL_KEY_ORDER = ("Ctrl", "Alt", "Shift", "Win")


class CancelKeySpec(object):
    """A parsed cancel key: the main key plus the modifiers that must be held.

    Compared with == for equality, repr()'d as its own label, so it round-trips
    through the session file and the DSH config as plain text.
    """

    __slots__ = ("vk", "name", "mods", "label")

    def __init__(self, vk, name, mods):
        self.vk = vk
        self.name = name
        self.mods = mods
        prefix = "+".join(_CANONICAL_MOD[m] for m in _mods_in_order(mods))
        self.label = prefix + "+" + name if prefix else name

    def __eq__(self, other):
        return (isinstance(other, CancelKeySpec)
                and self.vk == other.vk and self.mods == other.mods)

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        return hash((self.vk, self.mods))

    def __repr__(self):
        return self.label

    __str__ = __repr__


_CANONICAL_MOD = {
    MOD_CONTROL: "Ctrl", MOD_ALT: "Alt", MOD_SHIFT: "Shift", MOD_WIN: "Win",
}
_MOD_VK = {
    MOD_CONTROL: VK_CONTROL_, MOD_ALT: VK_ALT_,
    MOD_SHIFT: VK_SHIFT_, MOD_WIN: VK_LWIN_,
}


def _mods_in_order(mods):
    return [m for m in (MOD_CONTROL, MOD_ALT, MOD_SHIFT, MOD_WIN) if mods & m]


def parse_cancel_key(spec):
    """Parse 'esc', 'ESC', 'Ctrl+Alt+Esc', 'f8', 'shift+f8' into a CancelKeySpec.

    Raises ValueError with an actionable message for anything that cannot be a
    cancel key, so a typo in the DSH config fails at 'begin' instead of leaving
    the user with no way to take control back.
    """
    if not isinstance(spec, str):
        raise ValueError("cancel key must be a string like 'ESC' or 'Ctrl+Alt+Esc'")
    raw = spec.strip()
    if not raw:
        raise ValueError("cancel key is empty")
    parts = [p.strip().lower() for p in raw.split("+")]
    if any(not p for p in parts):
        raise ValueError("cancel key %r has an empty part" % spec)

    mods = 0
    main = None
    for p in parts:
        if p in _CANCEL_KEY_MODS:
            bit = _CANCEL_KEY_MODS[p][0]
            if mods & bit:
                raise ValueError("cancel key %r repeats a modifier" % spec)
            mods |= bit
            continue
        if main is not None:
            raise ValueError(
                "cancel key %r has more than one main key (%r and %r)"
                % (spec, main, p))
        main = p
    if main is None:
        raise ValueError(
            "cancel key %r is modifiers only; add a key, e.g. 'Ctrl+F8'" % spec)
    if main == "space":
        raise ValueError(
            "Space cannot be a cancel key: the app under control needs it")
    if main not in _CANCEL_KEY_NAMES:
        raise ValueError(
            "unknown key %r in cancel key %r; use a named key such as %s"
            % (main, spec, ", ".join(label for label, _ in _cancel_key_examples())))
    vk, label = _CANCEL_KEY_NAMES[main]
    return CancelKeySpec(vk, label, mods)


def _cancel_key_examples():
    """The keys a user can type in a cancel spec, deduplicated by vk, as
    (label, vk) sorted by label.

    Keys the hook cannot reliably see are deliberately absent: a cancel key that
    never fires is worse than no cancel key.
    """
    by_vk = {}
    for _name, (vk, label) in _CANCEL_KEY_NAMES.items():
        by_vk.setdefault(vk, label)
    return sorted((label, vk) for vk, label in by_vk.items())


def _sleep_ms(ms):
    if ms > 0:
        time.sleep(ms / 1000.0)


# ----------------------------------------------------------------------------
# Input helpers
# ----------------------------------------------------------------------------

def send_input(inputs):
    n = len(inputs)
    arr = (INPUT * n)(*inputs)
    sent = user32.SendInput(n, arr, ctypes.sizeof(INPUT))
    if sent != n:
        raise ctypes.WinError(ctypes.get_last_error())


def _mouse_input(flags, dx=0, dy=0, data=0):
    i = INPUT()
    i.type = INPUT_MOUSE
    i.union.mi = MOUSEINPUT(dx, dy, data, flags, 0, 0)
    return i


def _key_input(vk=0, scan=0, flags=0):
    i = INPUT()
    i.type = INPUT_KEYBOARD
    i.union.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return i


# ----------------------------------------------------------------------------
# Mouse
# ----------------------------------------------------------------------------

def cursor_position():
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return (pt.x, pt.y)


def move_cursor(x, y):
    user32.SetCursorPos(int(x), int(y))


def _click_button(x, y, down_flag, up_flag, clicks=1):
    """Move to (x, y) and click. SetCursorPos moves in absolute physical
    pixels (no SendInput absolute-coordinate normalization trap); SendInput
    delivers the button transitions."""
    move_cursor(x, y)
    _sleep_ms(EVENT_DELAY_MS)
    for _ in range(clicks):
        send_input([_mouse_input(down_flag)])
        _sleep_ms(EVENT_DELAY_MS)
        send_input([_mouse_input(up_flag)])
        _sleep_ms(EVENT_DELAY_MS)


_BUTTONS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}


def click(x, y, button="left", clicks=1):
    down, up = _BUTTONS[button]
    _click_button(x, y, down, up, clicks)


def drag(x1, y1, x2, y2, button="left"):
    down, up = _BUTTONS[button]
    move_cursor(x1, y1)
    _sleep_ms(EVENT_DELAY_MS)
    send_input([_mouse_input(down)])
    _sleep_ms(EVENT_DELAY_MS)
    # Interpolate waypoints so the drag is a continuous motion, not a teleport.
    steps = max(1, int(max(abs(x2 - x1), abs(y2 - y1)) / 12))
    for k in range(1, steps + 1):
        xi = x1 + (x2 - x1) * k // steps
        yi = y1 + (y2 - y1) * k // steps
        move_cursor(int(xi), int(yi))
        _sleep_ms(EVENT_DELAY_MS)
    move_cursor(int(x2), int(y2))
    _sleep_ms(EVENT_DELAY_MS)
    send_input([_mouse_input(up)])


def scroll(x, y, amount, horizontal=False):
    """Scroll at (x, y). amount is in notches: + up/right, - down/left."""
    move_cursor(x, y)
    _sleep_ms(EVENT_DELAY_MS)
    wheel_flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
    delta = int(amount) * 120  # WHEEL_DELTA = 120 per notch
    send_input([_mouse_input(wheel_flag, 0, 0, data=delta)])
    _sleep_ms(EVENT_DELAY_MS)


# ----------------------------------------------------------------------------
# Keyboard
# ----------------------------------------------------------------------------

_VK = {
    "backspace": 0x08, "tab": 0x09, "enter": 0x0D, "return": 0x0D,
    "shift": 0x10, "ctrl": 0x11, "control": 0x11, "alt": 0x12,
    "pause": 0x13, "capslock": 0x14, "esc": 0x1B, "escape": 0x1B,
    "space": 0x20, "spacebar": 0x20, "pageup": 0x21, "pagedown": 0x22,
    "end": 0x23, "home": 0x24, "left": 0x25, "up": 0x26, "right": 0x27,
    "down": 0x28, "printscreen": 0x2C, "insert": 0x2D, "delete": 0x2E,
    "del": 0x2E, "win": 0x5B, "windows": 0x5B, "meta": 0x5B,
    "apps": 0x5D, "numlock": 0x90, "scrolllock": 0x91,
    "f1": 0x70, "f2": 0x71, "f3": 0x72, "f4": 0x73, "f5": 0x74, "f6": 0x75,
    "f7": 0x76, "f8": 0x77, "f9": 0x78, "f10": 0x79, "f11": 0x7A, "f12": 0x7B,
}


def _char_vk(ch):
    """Resolve a single printable char to (vk, shift_needed) via VkKeyScanW."""
    res = user32.VkKeyScanW(ord(ch))
    if res == -1:
        raise ValueError(f"Cannot map character to a key: {ch!r}")
    vk = res & 0xFF
    shift = (res >> 8) & 0x1
    return vk, bool(shift)


def type_text(text):
    """Type arbitrary Unicode text via KEYEVENTF_UNICODE — independent of the
    active keyboard layout. Characters are batched into a single SendInput call
    (64 chars at a time, well under the OS batch limits) with NO per-key delay,
    so long payloads type fast instead of at a per-character cadence. One short
    settle per chunk, not per character."""
    events = []
    for ch in text:
        cp = ord(ch)
        events.append(_key_input(scan=cp, flags=KEYEVENTF_UNICODE))
        events.append(_key_input(scan=cp, flags=KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    CHUNK = 128  # 64 characters = 128 key events per SendInput batch
    for i in range(0, len(events), CHUNK):
        send_input(events[i:i + CHUNK])
        _sleep_ms(EVENT_DELAY_MS)


def _key_down(vk):
    send_input([_key_input(vk=vk, flags=0)])


def _key_up(vk):
    send_input([_key_input(vk=vk, flags=KEYEVENTF_KEYUP)])


def _tap(vk):
    _key_down(vk)
    _sleep_ms(EVENT_DELAY_MS)
    _key_up(vk)
    _sleep_ms(EVENT_DELAY_MS)


_MODS = ("shift", "ctrl", "control", "alt", "win", "windows", "meta")


def key_combo(spec):
    """Press a combo like 'ctrl+c', 'alt+tab', 'ctrl+shift+esc', or a single
    named key like 'enter'. Modifiers are held while the main key is tapped."""
    parts = [p.strip().lower() for p in spec.split("+") if p.strip()]
    if not parts:
        raise ValueError("Empty key spec")
    modifiers, main = [], None
    for p in parts:
        if p in _MODS:
            modifiers.append(p)
        else:
            if main is not None:
                raise ValueError(f"Multiple non-modifier keys in {spec!r}")
            main = p
    mod_vks = [_VK[m] for m in modifiers]

    for vk in mod_vks:
        _key_down(vk)
        _sleep_ms(EVENT_DELAY_MS)
    try:
        if main is not None:
            if len(main) == 1 and main.isprintable():
                vk, shift = _char_vk(main)
                if shift:
                    _key_down(_VK["shift"])
                    _sleep_ms(EVENT_DELAY_MS)
                _tap(vk)
                if shift:
                    _key_up(_VK["shift"])
                    _sleep_ms(EVENT_DELAY_MS)
            else:
                if main not in _VK:
                    raise ValueError(f"Unknown key name: {main!r}")
                _tap(_VK[main])
    finally:
        for vk in reversed(mod_vks):
            _key_up(vk)
            _sleep_ms(EVENT_DELAY_MS)


# ----------------------------------------------------------------------------
# Screenshot (Pillow). The process is DPI-aware, so returned pixels are
# physical pixels and match the cursor coordinate space 1:1.
# ----------------------------------------------------------------------------

def screenshot(path=None):
    from PIL import ImageGrab
    img = ImageGrab.grab(all_screens=True)
    if path:
        img.save(path)
    return img


# ----------------------------------------------------------------------------
# Overlay — layered, top-most, click-through, non-activating orange windows.
# A window is destroyed when its owning process exits, so a dead daemon can
# never leave a stuck highlight.
# ----------------------------------------------------------------------------

_WNDPROC = WNDPROC

_TEXT_BY_HWND = {}  # hwnd value -> banner text

_DT_LEFT = 0x0000
_DT_CENTER = 0x0001
_DT_VCENTER = 0x0004
_DT_SINGLELINE = 0x0020


def _window_proc(hwnd, msg, wparam, lparam):
    if msg == WM_PAINT:
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        try:
            text = _TEXT_BY_HWND.get(hwnd, "")
            if text:
                rect = RECT()
                user32.GetClientRect(hwnd, ctypes.byref(rect))
                gdi32.SetBkMode(hdc, 1)  # TRANSPARENT
                gdi32.SetTextColor(hdc, 0x00FFFFFF)
                font = gdi32.CreateFontW(
                    -BANNER_FONT_PX, 0, 0, 0, 700, 0, 0, 0, 1, 0, 0, 0, 0, "Segoe UI")
                old = gdi32.SelectObject(hdc, font)
                user32.DrawTextW(hdc, text, -1, ctypes.byref(rect),
                                 _DT_CENTER | _DT_VCENTER | _DT_SINGLELINE)
                gdi32.SelectObject(hdc, old)
                gdi32.DeleteObject(font)
        finally:
            user32.EndPaint(hwnd, ctypes.byref(ps))
        return 0
    if msg == WM_DESTROY:
        user32.PostQuitMessage(0)
        return 0
    return user32.DefWindowProcW(hwnd, msg, wparam, lparam)


_WNDPROC_FN = _WNDPROC(_window_proc)

_CLASS_ATOM = None
_HBRUSH = None


def _register_class():
    global _CLASS_ATOM, _HBRUSH
    if _CLASS_ATOM is not None:
        return
    r, g, b = ORANGE
    _HBRUSH = gdi32.CreateSolidBrush(r | (g << 8) | (b << 16))
    wndclass = WNDCLASS()
    wndclass.lpfnWndProc = _WNDPROC_FN
    wndclass.hInstance = kernel32.GetModuleHandleW(None)
    wndclass.lpszClassName = "DshComputerUseOverlay"
    wndclass.hbrBackground = _HBRUSH
    atom = user32.RegisterClassW(ctypes.byref(wndclass))
    if not atom:
        raise ctypes.WinError(ctypes.get_last_error())
    _CLASS_ATOM = atom


def _create_overlay_window(x, y, w, h, text=None, alpha=OVERLAY_ALPHA):
    _register_class()
    exstyle = (WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TRANSPARENT |
               WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
    hwnd = user32.CreateWindowExW(
        exstyle, "DshComputerUseOverlay", "", WS_POPUP,
        int(x), int(y), int(w), int(h), None, None,
        kernel32.GetModuleHandleW(None), None)
    if not hwnd:
        raise ctypes.WinError(ctypes.get_last_error())
    if text:
        _TEXT_BY_HWND[hwnd] = text
    user32.SetLayeredWindowAttributes(hwnd, 0, alpha, LWA_ALPHA)
    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    user32.UpdateWindow(hwnd)
    return hwnd


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", DWORD), ("rcMonitor", RECT), ("rcWork", RECT), ("dwFlags", DWORD)]


MONITORENUMPROC = ctypes.WINFUNCTYPE(BOOL, HANDLE, HDC, ctypes.POINTER(RECT), LPARAM)

user32.EnumDisplayMonitors.restype = BOOL
user32.EnumDisplayMonitors.argtypes = [HDC, ctypes.c_void_p, MONITORENUMPROC, LPARAM]
user32.GetMonitorInfoW.restype = BOOL
user32.GetMonitorInfoW.argtypes = [HANDLE, ctypes.POINTER(MONITORINFO)]


def _monitors():
    """Return one (x, y, w, h) rect per physical monitor. Falls back to the
    primary screen if enumeration fails."""
    rects = []

    def _cb(hmon, hdc, lprc, lparam):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
            r = mi.rcMonitor
            rects.append((r.left, r.top, r.right - r.left, r.bottom - r.top))
        return True

    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(_cb), 0)
    if not rects:
        rects.append((0, 0,
                      user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)))
    return rects


def _colorref(c):
    return c[0] | (c[1] << 8) | (c[2] << 16)


def _text_width(hdc, s):
    sz = SIZE()
    gdi32.GetTextExtentPoint32W(hdc, s, len(s), ctypes.byref(sz))
    return sz.cx


def _build_island_buffer(w, h, dot_cx, dot_cy, dot_r):
    """Dark neutral rounded 'island' with one small orange accent dot, per-pixel
    alpha and alpha-AA edges. A neutral surface + one small accent follows the
    60-30-10 colour rule, and a near-black surface gives white text ~16:1
    contrast (WCAG AAA) instead of a high-chroma fill that reads as garish."""
    import math
    r = h / 2.0
    cy = h / 2.0
    buf = bytearray(w * h * 4)
    br, bg, bb = ISLAND_BG
    dr, dg, db = ORANGE
    for y in range(h):
        for x in range(w):
            if x < r:
                d = math.hypot(x - r, y - cy) - r
            elif x > w - r:
                d = math.hypot(x - (w - r), y - cy) - r
            else:
                d = abs(y - cy) - r
            cov = 0.5 - d
            if cov <= 0.0:
                continue
            if cov > 1.0:
                cov = 1.0
            dd = math.hypot(x + 0.5 - dot_cx, y + 0.5 - dot_cy)
            dcov = 0.0
            if dd <= dot_r + 0.5:
                dcov = dot_r + 0.5 - dd
                if dcov > 1.0:
                    dcov = 1.0
                if dcov < 0.0:
                    dcov = 0.0
            r_ = br if dcov == 0.0 else int(br + (dr - br) * dcov)
            g_ = bg if dcov == 0.0 else int(bg + (dg - bg) * dcov)
            b_ = bb if dcov == 0.0 else int(bb + (db - bb) * dcov)
            edge = -d
            if edge < 1.5:               # subtle 1px light rim for definition
                mix = (1.5 - edge) / 1.5 * 0.5
                r_ = int(r_ + (92 - r_) * mix)
                g_ = int(g_ + (92 - g_) * mix)
                b_ = int(b_ + (100 - b_) * mix)
            i = (y * w + x) * 4
            buf[i] = b_
            buf[i + 1] = g_
            buf[i + 2] = r_
            buf[i + 3] = int(246 * cov)
    return bytes(buf)


def _create_island(center_x, y, main_text, hint_text):
    """Dynamic-Island-style pill: dark neutral surface, orange accent dot,
    white primary text, calmer gray hint. Sized to fit its content."""
    _register_class()
    h = ISLAND_HEIGHT
    font = gdi32.CreateFontW(-ISLAND_FONT_PX, 0, 0, 0, 600, 0, 0, 0, 1, 0, 0, 0, 0, "Segoe UI")
    font_hint = gdi32.CreateFontW(-ISLAND_FONT_PX, 0, 0, 0, 400, 0, 0, 0, 1, 0, 0, 0, 0, "Segoe UI")

    hdc_m = user32.GetDC(None)
    old_f = gdi32.SelectObject(hdc_m, font)
    sep = "   ·   "
    main_w = _text_width(hdc_m, main_text)
    gdi32.SelectObject(hdc_m, font_hint)
    sep_w = _text_width(hdc_m, sep)
    hint_w = _text_width(hdc_m, hint_text)
    gdi32.SelectObject(hdc_m, old_f)
    user32.ReleaseDC(None, hdc_m)

    pad_left, dot_d, gap, pad_right = 18, 9, 12, 20
    w = pad_left + dot_d + gap + main_w + sep_w + hint_w + pad_right
    x = int(center_x - w // 2)
    main_x = pad_left + dot_d + gap
    sep_x = main_x + main_w

    hwnd = user32.CreateWindowExW(
        WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
        "DshComputerUseOverlay", "", WS_POPUP, x, int(y), int(w), h,
        None, None, kernel32.GetModuleHandleW(None), None)
    if not hwnd:
        gdi32.DeleteObject(font)
        gdi32.DeleteObject(font_hint)
        raise ctypes.WinError(ctypes.get_last_error())

    buf = _build_island_buffer(w, h, pad_left + dot_d / 2.0, h / 2.0, dot_d / 2.0)
    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0
    ppv = ctypes.c_void_p()
    hdib = gdi32.CreateDIBSection(None, ctypes.byref(bmi), 0, ctypes.byref(ppv), None, 0)
    if not hdib:
        gdi32.DeleteObject(font)
        gdi32.DeleteObject(font_hint)
        user32.DestroyWindow(hwnd)
        raise ctypes.WinError(ctypes.get_last_error())
    ctypes.memmove(ppv, buf, len(buf))

    hdc_mem = gdi32.CreateCompatibleDC(None)
    old_bmp = gdi32.SelectObject(hdc_mem, hdib)
    old_f2 = gdi32.SelectObject(hdc_mem, font)
    gdi32.SetBkMode(hdc_mem, 1)
    flags = _DT_LEFT | _DT_VCENTER | _DT_SINGLELINE

    gdi32.SetTextColor(hdc_mem, _colorref(ISLAND_TEXT))
    r1 = RECT(main_x, 0, w, h)
    user32.DrawTextW(hdc_mem, main_text, -1, ctypes.byref(r1), flags)

    gdi32.SelectObject(hdc_mem, font_hint)
    gdi32.SetTextColor(hdc_mem, _colorref(ISLAND_HINT))
    r2 = RECT(sep_x, 0, w, h)
    user32.DrawTextW(hdc_mem, sep + hint_text, -1, ctypes.byref(r2), flags)
    gdi32.SelectObject(hdc_mem, font)

    hdc_screen = user32.GetDC(None)
    blend = BLENDFUNCTION(0, 0, 255, AC_SRC_ALPHA)
    dst = POINT(x, int(y))
    size = SIZE(w, h)
    src = POINT(0, 0)
    user32.UpdateLayeredWindow(
        hwnd, hdc_screen, ctypes.byref(dst), ctypes.byref(size),
        hdc_mem, ctypes.byref(src), 0, ctypes.byref(blend), ULW_ALPHA)
    user32.ReleaseDC(None, hdc_screen)

    gdi32.SelectObject(hdc_mem, old_f2)
    gdi32.SelectObject(hdc_mem, old_bmp)
    gdi32.DeleteDC(hdc_mem)
    gdi32.DeleteObject(hdib)
    gdi32.DeleteObject(font)
    gdi32.DeleteObject(font_hint)
    user32.ShowWindow(hwnd, SW_SHOWNOACTIVATE)
    return hwnd


class Overlay:
    """A set of overlay windows. destroy() removes them all."""

    def __init__(self):
        self.hwnds = []

    def bar(self, x, y, w, h, alpha=OVERLAY_ALPHA):
        self.hwnds.append(_create_overlay_window(x, y, w, h, alpha=alpha))

    def banner(self, main_text, hint_text, y=None):
        """One island pill at the top-center of every monitor."""
        for (mx, my, mw, mh) in _monitors():
            yy = (my + 16) if y is None else (my + y)
            self.hwnds.append(_create_island(mx + mw // 2, yy, main_text, hint_text))

    def box(self, cx, cy, w=48, h=48, thickness=4):
        x0 = int(cx - w / 2)
        y0 = int(cy - h / 2)
        self.bar(x0, y0, w, thickness)                    # top
        self.bar(x0, y0 + h - thickness, w, thickness)    # bottom
        self.bar(x0, y0, thickness, h)                    # left
        self.bar(x0 + w - thickness, y0, thickness, h)    # right

    def destroy(self):
        for hwnd in self.hwnds:
            try:
                user32.DestroyWindow(hwnd)
            except Exception:
                pass
        for hwnd in self.hwnds:
            _TEXT_BY_HWND.pop(hwnd, None)
        self.hwnds = []


def virtual_screen_rect():
    x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return x, y, w, h


# ----------------------------------------------------------------------------
# Session state (stop flag, token, daemon pid)
# ----------------------------------------------------------------------------

def _read_session():
    if not os.path.exists(SESSION_FILE):
        return None
    try:
        with open(SESSION_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _write_session(state):
    os.makedirs(SESSION_DIR, exist_ok=True)
    tmp = SESSION_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f)
    os.replace(tmp, SESSION_FILE)


def _daemon_alive(pid):
    if pid is None:
        return False
    h = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION | SYNCHRONIZE, False, int(pid))
    if not h:
        return False
    code = DWORD(0)
    kernel32.GetExitCodeProcess(h, ctypes.byref(code))
    kernel32.CloseHandle(h)
    return code.value == STILL_ACTIVE


def _require_active_session():
    s = _read_session()
    if s is None:
        print("ERROR: no active control session. Run 'begin' first.", file=sys.stderr)
        sys.exit(2)
    if s.get("stop_requested"):
        print("CONTROL_RELEASED: user requested stop. Aborting action.", file=sys.stderr)
        sys.exit(3)
    if not _daemon_alive(s.get("pid")):
        print("WARNING: daemon not running; overlays and hotkey may be inactive.",
              file=sys.stderr)
    return s


def _hotkey_label():
    """The effective cancel key, as it shows in the island and in 'begin'."""
    return resolve_cancel_key().label


def _cancel_key_source():
    """Where the effective cancel key came from: cli / env / config / default."""
    return resolve_cancel_key()[1]


def resolve_cancel_key(explicit=None):
    """Resolve the effective cancel key. Precedence: explicit (CLI) >
    COMPUTER_USE_CANCEL_KEY > the DSH config file > DEFAULT_CANCEL_KEY.

    Returns (CancelKeySpec, source). An invalid spec raises ValueError even when
    a lower-precedence value is valid: silently ignoring the user's setting
    would hand them a cancel key they did not choose.
    """
    if explicit:
        return parse_cancel_key(explicit), "cli"
    env = os.environ.get("COMPUTER_USE_CANCEL_KEY")
    if env and env.strip():
        return parse_cancel_key(env), "env"
    cfg = _read_config().get("cancel_key")
    if cfg and isinstance(cfg, str) and cfg.strip():
        return parse_cancel_key(cfg), "config"
    return parse_cancel_key(DEFAULT_CANCEL_KEY), "default"


AUDIT_FILE = os.path.join(SESSION_DIR, "audit.jsonl")


def _audit(action, **fields):
    """Append one structured, human-readable audit record per action, so every
    move/click/type is traceable (who did what, when, with what result) — the
    RPA-grade audit trail that makes a desktop-automation skill safe to trust."""
    try:
        os.makedirs(SESSION_DIR, exist_ok=True)
        rec = {
            "ts": time.time(),
            "action": action,
            "pid": os.getpid(),
        }
        rec.update(fields)
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass


def _screen_diff_pct():
    """Return the fraction of screen that changed between two quick grabs, as a
    cheap stability signal. Downsampled to keep it fast."""
    from PIL import Image
    a = screenshot()
    _sleep_ms(120)
    b = screenshot()
    small = (64, 36)
    ga = a.convert("L").resize(small)
    gb = b.convert("L").resize(small)
    import math
    total = 0.0
    for (pa, pb) in zip(ga.getdata(), gb.getdata()):
        total += abs(pa - pb)
    return total / (255.0 * small[0] * small[1])


def _wait_stable(timeout_ms=8000, threshold=0.001, poll_ms=120):
    """Wait until the screen stops changing (two consecutive grabs nearly
    identical) or the timeout elapses. This is the 'act then observe' settle:
    it lets the target finish redrawing after a click, without a blind sleep."""
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        if _screen_diff_pct() <= threshold:
            return True
    return False


def _session_state(token, stop_requested=False, pid=None, cancel_key=None,
                   cancel_key_source=None):
    spec = cancel_key or parse_cancel_key(DEFAULT_CANCEL_KEY)
    return {
        "token": token,
        "pid": pid,
        "started_at": time.time(),
        "stop_requested": stop_requested,
        "released": False,
        "color": f"{ORANGE[0]:02X}{ORANGE[1]:02X}{ORANGE[2]:02X}",
        "cancel_key": spec.label,
        "cancel_key_source": cancel_key_source,
        # Kept for older readers of the session file.
        "hotkey": spec.label,
    }


def _session_cancel_key(state):
    """The cancel key this session was started with, re-parsed from its label."""
    label = state.get("cancel_key") or state.get("hotkey") or DEFAULT_CANCEL_KEY
    try:
        return parse_cancel_key(label)
    except ValueError:
        return parse_cancel_key(DEFAULT_CANCEL_KEY)


# ----------------------------------------------------------------------------
# Daemon — island pill on every monitor + global cancel key + stop flag
# ----------------------------------------------------------------------------

class CancelKeyHook(object):
    """Global cancel key via a WH_KEYBOARD_LL low-level keyboard hook.

    Why a hook and not RegisterHotKey:
      * RegisterHotKey cannot take a bare ESC (Windows reserves it), and the
        default cancel key is now ESC.
      * RegisterHotKey never tells you *which* key produced the hotkey, so the
        same combo bound to two sessions fires only the first one. The hook
        knows the vkCode, so each session ignores the other's combo.

    Two hard rules:
      * Injected events are never treated as the cancel key. The engine types
        and presses keys with SendInput; without this rule, any agent `key`
        command that matched the cancel combo would cancel its own session.
      * The hook callback returns immediately and never touches the device, so
        it cannot add perceptible input latency. It is also a hard deadline:
        Windows silently unhooks a callback that takes too long.
    """

    def __init__(self, spec, on_cancel):
        self.spec = spec
        self.on_cancel = on_cancel
        self._proc = HOOKPROC(self._callback)   # keep a reference: no GC
        self.handle = None
        self.error = None

    def _modifiers_down(self):
        spec_mods = self.spec.mods
        for bit, vk in _MOD_VK.items():
            down = bool(user32.GetAsyncKeyState(vk) & KEY_PRESSED)
            if bit == MOD_WIN and not down:
                down = bool(user32.GetAsyncKeyState(VK_RWIN_) & KEY_PRESSED)
            if bool(spec_mods & bit) != down:
                return False
        return True

    def _callback(self, ncode, wparam, lparam):
        try:
            if ncode >= 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
                kb = ctypes.cast(lparam,
                                 ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if (kb.vkCode == self.spec.vk
                        and not (kb.flags & LLKHF_INJECTED)
                        and self._modifiers_down()):
                    self.on_cancel()
        except Exception:
            pass  # a raising hook callback would unhook us; never let that happen
        return user32.CallNextHookEx(None, ncode, wparam, lparam)

    def install(self):
        self.handle = user32.SetWindowsHookExW(
            WH_KEYBOARD_LL, self._proc, kernel32.GetModuleHandleW(None), 0)
        if not self.handle:
            self.error = ctypes.get_last_error()
        return bool(self.handle)

    def uninstall(self):
        if self.handle:
            try:
                user32.UnhookWindowsHookEx(self.handle)
            except Exception:
                pass
            self.handle = None


def _daemon_main(token, cancel_key):
    """Own the island, the cancel-key hook, and the stop flag until told to stop.

    Order matters: the hook is installed AFTER the thread message queue exists
    (the overlay already created it) and the session is only rewritten with
    hotkey_registered once the hook call returned, so 'begin' can report a
    cancel key that failed to install instead of claiming one that does not work.
    """
    overlay = Overlay()
    overlay.banner("Deepseek using your computer", f"{cancel_key.label} to cancel")

    state = {"cancelled": False}

    def _on_cancel():
        state["cancelled"] = True

    hook = CancelKeyHook(cancel_key, _on_cancel)
    registered = hook.install()
    _update_session(hotkey_registered=registered, hotkey_error=hook.error)

    msg = MSG()
    stop = False
    while not stop:
        # Poll the stop flag directly (robust against blocked GetMessage): both
        # the cancel key and an external 'end' tear the daemon down within ~100 ms.
        s = _read_session()
        if s is None or s.get("stop_requested") or s.get("token") != token:
            break
        if state["cancelled"]:
            cancelled = _session_state(token, stop_requested=True,
                                       cancel_key=cancel_key)
            cancelled["hotkey_registered"] = registered
            _write_session(cancelled)
            stop = True
            break
        # Pump the thread queue. The low-level hook delivers by posting to this
        # thread, so an unpumped queue would stall every keypress system-wide.
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_REMOVE):
            if msg.message == WM_QUIT:
                stop = True
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        if stop:
            break
        _sleep_ms(50)

    hook.uninstall()
    overlay.destroy()
    s = _read_session()
    if s and s.get("token") == token:
        s["released"] = True
        _write_session(s)


def _update_session(**fields):
    s = _read_session()
    if s is not None:
        s.update(fields)
        _write_session(s)


# ----------------------------------------------------------------------------
# Subcommands
# ----------------------------------------------------------------------------

def cmd_begin(args):
    token = args.token or ("%016x" % random.getrandbits(64))
    try:
        cancel_key, source = resolve_cancel_key(getattr(args, "cancel_key", None))
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print("Fix with: computer_use.py config --cancel-key \"Ctrl+Alt+Esc\"",
              file=sys.stderr)
        sys.exit(2)
    os.makedirs(SESSION_DIR, exist_ok=True)
    _write_session(_session_state(token, stop_requested=False, pid=None,
                                  cancel_key=cancel_key,
                                  cancel_key_source=source))

    creationflags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    proc = subprocess.Popen(
        [sys.executable, os.path.abspath(__file__), "daemon", "--token", token,
         "--cancel-key", cancel_key.label],
        creationflags=creationflags,
        close_fds=True,
    )
    for _ in range(20):
        _sleep_ms(50)
        s = _read_session()
        if s and s.get("pid"):
            break
    s = _read_session() or {}
    if not s.get("pid"):
        _write_session({**s, "pid": proc.pid})

    # Give the daemon a moment to install the hook, then report the truth: a
    # cancel key that did not install is the one thing the user must know.
    hooked = None
    for _ in range(20):
        _sleep_ms(50)
        s = _read_session() or {}
        if "hotkey_registered" in s:
            hooked = bool(s.get("hotkey_registered"))
            break
    _audit("begin", token=token, cancel_key=cancel_key.label,
           cancel_key_source=source, cancel_key_live=hooked)

    print("Control acquired.")
    print(f"Overlay: island pill on every monitor "
          f"(accent #{ORANGE[0]:02X}{ORANGE[1]:02X}{ORANGE[2]:02X}).")
    print(f"Cancel key: {cancel_key.label} (source: {source}; "
          f"works anywhere, even if the agent stops).")
    if hooked is False:
        print("WARNING: the cancel key hook did not install; 'end' still releases "
              "control from this agent.", file=sys.stderr)
    print(f"Session token: {token}")
    print("Always run 'end' when finished to release control.")


def cmd_daemon(args):
    token = args.token
    cancel_key = parse_cancel_key(args.cancel_key)
    _write_session(_session_state(token, stop_requested=False, pid=os.getpid(),
                                  cancel_key=cancel_key))
    try:
        _daemon_main(token, cancel_key)
    finally:
        cur = _read_session()
        if cur and cur.get("token") == token:
            cur["released"] = True
            _write_session(cur)


def cmd_end(args):
    s = _read_session()
    if s is None:
        print("No active control session.")
        return
    _write_session({**s, "stop_requested": True})
    for _ in range(30):
        if not _daemon_alive(s.get("pid")):
            break
        _sleep_ms(50)
    try:
        os.remove(SESSION_FILE)
    except OSError:
        pass
    print("Control released. Overlay cleared, cancel key unregistered.")


def cmd_status(args):
    s = _read_session()
    if s is None:
        print("status: idle (no active control session)")
        return
    alive = _daemon_alive(s.get("pid"))
    print(json.dumps({
        "active": alive and not s.get("released"),
        "daemon_alive": alive,
        "stop_requested": s.get("stop_requested", False),
        "pid": s.get("pid"),
        "token": s.get("token"),
        "color": s.get("color"),
        "hotkey": s.get("cancel_key") or s.get("hotkey"),
        "cancel_key": s.get("cancel_key") or s.get("hotkey"),
        "cancel_key_source": s.get("cancel_key_source"),
        "cancel_key_live": s.get("hotkey_registered"),
    }, indent=2))


def cmd_config(args):
    """Read or change the DSH-side setting: the cancel key."""
    path = config_path()
    if args.show_path and args.cancel_key is None and not args.reset:
        print(path)
        return
    if args.reset:
        cfg = _read_config()
        cfg.pop("cancel_key", None)
        _write_config(cfg)
        print(f"Removed the stored cancel key from {path}. Default is now in effect.")
    elif args.cancel_key is not None:
        try:
            spec = parse_cancel_key(args.cancel_key)
        except ValueError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            sys.exit(2)
        cfg = _read_config()
        cfg["cancel_key"] = spec.label
        _write_config(cfg)
        print(f"Cancel key set to {spec.label} in {path}.")
        print("Takes effect on the next 'begin'.")

    try:
        spec, source = resolve_cancel_key()
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        print(f"Config file: {path}", file=sys.stderr)
        sys.exit(2)
    cfg = _read_config()
    print(json.dumps({
        "config_path": path,
        "config_exists": os.path.exists(path),
        "cancel_key_setting": cfg.get("cancel_key"),
        "effective_cancel_key": spec.label,
        "effective_source": source,
        "default_cancel_key": parse_cancel_key(DEFAULT_CANCEL_KEY).label,
        "env_override": os.environ.get("COMPUTER_USE_CANCEL_KEY") or None,
    }, indent=2))


def cmd_screenshot(args):
    out = args.out or os.path.join(SESSION_DIR, "screen.png")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    img = screenshot(out)
    print(f"{img.size[0]}x{img.size[1]} {os.path.abspath(out)}")


def cmd_cursor_position(args):
    x, y = cursor_position()
    print(f"{x} {y}")


def cmd_move(args):
    _require_active_session()
    move_cursor(args.x, args.y)
    _audit("move", x=args.x, y=args.y)
    print(f"moved {args.x} {args.y}")


def _with_target_highlight(x, y, w, h, fn):
    """Show a transient orange box at the target, run fn, linger, clear."""
    ov = Overlay()
    ov.box(x, y, w=w, h=h)
    try:
        fn()
        _sleep_ms(HIGHLIGHT_LINGER_MS)
    finally:
        ov.destroy()


def cmd_click(args):
    _require_active_session()
    _with_target_highlight(args.x, args.y, args.w, args.h,
                           lambda: click(args.x, args.y, args.button, args.clicks))
    _audit("click", x=args.x, y=args.y, button=args.button, clicks=args.clicks)
    print(f"clicked {args.x} {args.y} button={args.button} clicks={args.clicks}")


def cmd_drag(args):
    _require_active_session()
    _with_target_highlight(args.x2, args.y2, args.w, args.h,
                           lambda: drag(args.x1, args.y1, args.x2, args.y2, args.button))
    _audit("drag", x1=args.x1, y1=args.y1, x2=args.x2, y2=args.y2, button=args.button)
    print(f"dragged {args.x1} {args.y1} -> {args.x2} {args.y2}")


def cmd_scroll(args):
    _require_active_session()
    _with_target_highlight(args.x, args.y, args.w, args.h,
                           lambda: scroll(args.x, args.y, args.amount, args.horizontal))
    _audit("scroll", x=args.x, y=args.y, amount=args.amount, horizontal=args.horizontal)
    print(f"scrolled {args.amount} at {args.x} {args.y}")


def cmd_type(args):
    _require_active_session()
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            text = f.read()
    else:
        text = args.text
    if text is None:
        print("ERROR: provide --text or --file", file=sys.stderr)
        sys.exit(2)
    type_text(text)
    _audit("type", chars=len(text))
    print(f"typed {len(text)} chars")


def cmd_key(args):
    _require_active_session()
    key_combo(args.combo)
    _audit("key", combo=args.combo)
    print(f"pressed {args.combo}")


def cmd_highlight(args):
    _require_active_session()
    # A short preview so the model can verify placement; the durable, per-action
    # highlight is the transient box drawn by click/move/scroll/drag.
    ov = Overlay()
    ov.box(args.x, args.y, args.w, args.h)
    _sleep_ms(1000)
    ov.destroy()
    print(f"highlighted {args.x} {args.y} (1 s preview)")


def cmd_clear_highlight(args):
    _require_active_session()
    print("highlight cleared")


def cmd_wait(args):
    _require_active_session()
    if args.stable:
        ok = _wait_stable(timeout_ms=args.timeout, threshold=args.threshold)
        print("stable" if ok else "timeout")
    else:
        _sleep_ms(args.ms)
        print(f"waited {args.ms} ms")


def cmd_self_test(args):
    print("== DeepSeek Computer Use self-test ==")
    print(f"Python: {sys.version.split()[0]}")
    print(f"Orange color: #{ORANGE[0]:02X}{ORANGE[1]:02X}{ORANGE[2]:02X}  alpha={OVERLAY_ALPHA}")
    print(f"Inter-event delay: {EVENT_DELAY_MS} ms")
    try:
        spec, source = resolve_cancel_key()
        print(f"Cancel key: {spec.label}  (source: {source})")
        print(f"Cancel key config: {config_path()}")
    except ValueError as e:
        spec, source = None, None
        print(f"Cancel key: INVALID ({e})")
    vx, vy, vw, vh = virtual_screen_rect()
    print(f"Virtual screen: x={vx} y={vy} w={vw} h={vh}  ({vw}x{vh} physical px)")
    x, y = cursor_position()
    print(f"Cursor position: ({x}, {y})")
    try:
        img = screenshot()
        print(f"Screenshot: OK ({img.size[0]}x{img.size[1]})")
    except Exception as e:
        print(f"Screenshot: FAILED ({e})")
    # Prove the hook can be installed and torn down without a session, so a
    # binding that would silently fail at 'begin' shows up here instead.
    if spec is not None:
        probe = CancelKeyHook(spec, lambda: None)
        ok = probe.install()
        probe.uninstall()
        print(f"Cancel key hook: {'OK (installed + removed)' if ok else 'FAILED (Win32 %s)' % probe.error}")
    ov = Overlay()
    ov.banner("Deepseek using your computer",
              f"{(spec or parse_cancel_key(DEFAULT_CANCEL_KEY)).label} to cancel")
    _sleep_ms(500)
    ov.destroy()
    print("Overlay flash: OK (destroyed)")
    print("Input injection NOT exercised by self_test (would move your real pointer).")


def _build_parser():
    p = argparse.ArgumentParser(prog="computer_use",
                                description="Windows screen/mouse/keyboard control engine")
    sub = p.add_subparsers(dest="command", required=True)

    b = sub.add_parser("begin", help="acquire control (island pill + cancel key)")
    b.add_argument("--token")
    b.add_argument("--cancel-key", metavar="SPEC",
                   help="cancel key for this session only, e.g. ESC or Ctrl+Alt+Esc "
                        "(default: the DSH setting, else ESC)")
    b.set_defaults(fn=cmd_begin)

    d = sub.add_parser("daemon", help="internal: run the overlay/cancel-key loop")
    d.add_argument("--token", required=True)
    d.add_argument("--cancel-key", default=DEFAULT_CANCEL_KEY)
    d.set_defaults(fn=cmd_daemon)

    e = sub.add_parser("end", help="release control, clear overlays")
    e.set_defaults(fn=cmd_end)

    st = sub.add_parser("status", help="show control session state")
    st.set_defaults(fn=cmd_status)

    cf = sub.add_parser("config", help="show or change the DSH setting (cancel key)")
    cf.add_argument("--cancel-key", metavar="SPEC",
                    help="set the cancel key persistently, e.g. ESC or Ctrl+Alt+Esc")
    cf.add_argument("--reset", action="store_true",
                    help="remove the stored setting and fall back to the default (ESC)")
    cf.add_argument("--show-path", action="store_true",
                    help="print only the config file path and exit")
    cf.set_defaults(fn=cmd_config)

    sc = sub.add_parser("screenshot", help="capture screen to PNG")
    sc.add_argument("--out", help="output path; default <tmp>/computer_use/screen.png")
    sc.set_defaults(fn=cmd_screenshot)

    cp = sub.add_parser("cursor_position", help="print current cursor x y")
    cp.set_defaults(fn=cmd_cursor_position)

    mv = sub.add_parser("move", help="move cursor to x y (instant)")
    mv.add_argument("x", type=int)
    mv.add_argument("y", type=int)
    mv.set_defaults(fn=cmd_move)

    cl = sub.add_parser("click", help="highlight target, move, click")
    cl.add_argument("x", type=int)
    cl.add_argument("y", type=int)
    cl.add_argument("--button", choices=["left", "right", "middle"], default="left")
    cl.add_argument("--clicks", type=int, default=1)
    cl.add_argument("--w", type=int, default=48)
    cl.add_argument("--h", type=int, default=48)
    cl.set_defaults(fn=cmd_click)

    dr = sub.add_parser("drag", help="mouse-down at x1 y1, drag to x2 y2")
    dr.add_argument("x1", type=int)
    dr.add_argument("y1", type=int)
    dr.add_argument("x2", type=int)
    dr.add_argument("y2", type=int)
    dr.add_argument("--button", choices=["left", "right", "middle"], default="left")
    dr.add_argument("--w", type=int, default=48)
    dr.add_argument("--h", type=int, default=48)
    dr.set_defaults(fn=cmd_drag)

    sw = sub.add_parser("scroll", help="scroll at x y (notches, +up -down)")
    sw.add_argument("x", type=int)
    sw.add_argument("y", type=int)
    sw.add_argument("amount", type=int)
    sw.add_argument("--horizontal", action="store_true")
    sw.add_argument("--w", type=int, default=48)
    sw.add_argument("--h", type=int, default=48)
    sw.set_defaults(fn=cmd_scroll)

    ty = sub.add_parser("type", help="type Unicode text")
    ty.add_argument("--text", help="text to type")
    ty.add_argument("--file", help="read text from a file")
    ty.set_defaults(fn=cmd_type)

    ky = sub.add_parser("key", help="press a combo or named key, e.g. 'ctrl+c' or 'enter'")
    ky.add_argument("combo")
    ky.set_defaults(fn=cmd_key)

    hl = sub.add_parser("highlight", help="preview a target box at x y")
    hl.add_argument("x", type=int)
    hl.add_argument("y", type=int)
    hl.add_argument("--w", type=int, default=48)
    hl.add_argument("--h", type=int, default=48)
    hl.set_defaults(fn=cmd_highlight)

    ch = sub.add_parser("clear_highlight", help="remove persistent highlight")
    ch.set_defaults(fn=cmd_clear_highlight)

    wt = sub.add_parser("wait", help="settle: --stable waits for the screen to stop changing")
    wt.add_argument("--stable", action="store_true",
                    help="wait until the screen stops changing (act-then-observe)")
    wt.add_argument("--timeout", type=int, default=8000, help="max ms for --stable")
    wt.add_argument("--threshold", type=float, default=0.001,
                    help="fraction of changed pixels below which the screen counts as stable")
    wt.add_argument("--ms", type=int, default=0, help="plain wait in ms (rarely needed)")
    wt.set_defaults(fn=cmd_wait)

    ss = sub.add_parser("self_test", help="non-destructive diagnostics")
    ss.set_defaults(fn=cmd_self_test)

    return p


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    args.fn(args)


if __name__ == "__main__":
    main()
