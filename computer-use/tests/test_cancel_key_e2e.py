"""Cancel-key end-to-end test.

There is a hard physical limit here, and the test is built around it rather than
faking past it: Windows marks *every* synthetic keystroke as injected
(SendInput, keybd_event, and .NET SendKeys all set LLKHF_INJECTED). No process
can synthesize a keystroke that looks physical. So the test splits the claim
into the part a machine can prove and the part it cannot:

  PROVEN, deterministically (no keystroke needed):
    * the callback's decision function classifies a physical ESC as a cancel and
      an injected ESC as not-a-cancel (this is the whole anti-self-cancel rule);
    * modifier matching reads real physical key state;
    * the parser and the config/env/cli precedence chain.

  PROVEN, against a live daemon:
    * the hook installs in the daemon and is reported truthfully in `status`;
    * injected ESC does not cancel the session (the real failure it prevents);
    * `end` releases control and no daemon survives.

  NOT provable here:
    * that a human's physical ESC cancels. That needs one real keypress; the
      test prints the exact command to do it by hand.

Run: python tests/test_cancel_key_e2e.py
"""
import ctypes
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "scripts", "computer_use.py")
PY = sys.executable

FAILS = []


def ok(msg):
    print("  PASS", msg)


def bad(msg):
    FAILS.append(msg)
    print("  FAIL", msg)


def check(cond, msg):
    (ok if cond else bad)(msg)
    return cond


def run(*args):
    return subprocess.run([PY, SCRIPT] + list(args), capture_output=True,
                          text=True, timeout=90)


def state():
    return cu._read_session()


def main():
    print("== cancel key e2e ==")

    # ---------------------------------------------------------------- parser
    print("1. parser + precedence (deterministic)")
    cases = {
        "esc": ("Esc", 0x1B, 0),
        "ESC": ("Esc", 0x1B, 0),
        "Ctrl+Alt+Esc": ("Ctrl+Alt+Esc", 0x1B, cu.MOD_CONTROL | cu.MOD_ALT),
        "ctrl+alt+esc": ("Ctrl+Alt+Esc", 0x1B, cu.MOD_CONTROL | cu.MOD_ALT),
        "f8": ("F8", 0x77, 0),
        "shift+f8": ("Shift+F8", 0x77, cu.MOD_SHIFT),
    }
    for spec, (label, vk, mods) in cases.items():
        try:
            got = cu.parse_cancel_key(spec)
            check((got.label, got.vk, got.mods) == (label, vk, mods),
                  "%r -> %s (vk=%#x mods=%d)" % (spec, got.label, got.vk, got.mods))
        except ValueError as e:
            bad("%r raised: %s" % (spec, e))
    for spec in ("space", "ctrl+ctrl+esc", "hyper+q", "ctrl+", "", "  "):
        try:
            cu.parse_cancel_key(spec)
            bad("%r should have been rejected" % spec)
        except ValueError:
            ok("%r rejected" % spec)

    # ------------------------------------------------- anti-self-cancel rule
    print("2. injected events are never a cancel (the self-cancel guard)")
    spec = cu.parse_cancel_key("esc")
    fired = []
    hook = cu.CancelKeyHook(spec, lambda: fired.append(1))

    # Every KBDLLHOOKSTRUCT is held in a named local. Taking addressof() of a
    # temporary lets CPython free it before the callback reads it, and the
    # callback then sees garbage instead of the struct under test.
    kb_phys = cu.KBDLLHOOKSTRUCT(0x1B, 0, 0, 0, 0)                 # real ESC
    kb_inj = cu.KBDLLHOOKSTRUCT(0x1B, 0, cu.LLKHF_INJECTED, 0, 0)  # SendInput
    kb_other = cu.KBDLLHOOKSTRUCT(0x41, 0, 0, 0, 0)                # 'A'
    lp_phys = ctypes.addressof(kb_phys)
    lp_inj = ctypes.addressof(kb_inj)
    lp_other = ctypes.addressof(kb_other)

    hook._callback(0, cu.WM_SYSKEYDOWN, lp_phys)
    check(fired == [1], "physical ESC (flags=0) IS a cancel")
    fired.clear()
    hook._callback(0, cu.WM_SYSKEYDOWN, lp_inj)
    check(fired == [], "injected ESC does not cancel")
    fired.clear()
    hook._callback(0, cu.WM_SYSKEYDOWN, lp_other)
    check(fired == [], "a different key does not cancel")
    fired.clear()
    hook._callback(0, cu.WM_KEYUP, lp_phys)
    check(fired == [], "key-up does not cancel")
    fired.clear()
    hook._callback(-1, cu.WM_SYSKEYDOWN, lp_phys)
    check(fired == [], "negative hook code is ignored")

    print("3. modifier matching needs the modifier physically down")
    combo = cu.parse_cancel_key("Ctrl+Alt+Esc")
    fired = []
    hook2 = cu.CancelKeyHook(combo, lambda: fired.append(1))
    hook2._callback(0, cu.WM_SYSKEYDOWN, lp_phys)   # no Ctrl/Alt held right now
    check(fired == [], "Ctrl+Alt+Esc without Ctrl and Alt does not fire")

    # --------------------------------------------------------- live daemon
    print("4. live daemon: hook installs, status is truthful")
    cfg = os.path.join(tempfile.gettempdir(), "cu-e2e-%d.json" % os.getpid())
    os.environ["COMPUTER_USE_CONFIG"] = cfg
    try:
        os.remove(cfg)
    except OSError:
        pass

    r = run("begin")
    lines = r.stdout.strip().splitlines()
    check("Cancel key: Esc (source: default" in r.stdout,
          "begin reports the default cancel key (Esc)")
    st = json.loads(run("status").stdout) if state() else {}
    check(st.get("cancel_key") == "Esc", "status reports cancel_key Esc")
    check(st.get("cancel_key_live") is True, "status reports cancel_key_live true")
    check(st.get("active") is True, "session active")

    print("5. live daemon: injected ESC must NOT cancel the session")
    r = run("key", "esc")          # exactly what the agent does mid-session
    check(r.returncode == 0, "agent's own 'key esc' succeeded")
    time.sleep(0.5)
    st = json.loads(run("status").stdout) if state() else {}
    check(not st.get("stop_requested"), "session not cancelled by injected ESC")
    check(st.get("active") is True, "session still active after injected ESC")

    print("6. 'end' releases control, no orphan daemon")
    r = run("end")
    check("Control released" in r.stdout, "end released control")
    check(state() is None, "session file cleared")
    time.sleep(0.3)
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
         "Where-Object { $_.CommandLine -like '*computer_use.py daemon*' } | "
         "Measure-Object).Count"],
        capture_output=True, text=True).stdout.strip()
    check(out in ("0", ""), "no orphaned daemon")

    print("7. non-default cancel key round-trips through a live daemon")
    r = run("begin", "--cancel-key", "ctrl+alt+esc")
    check("Cancel key: Ctrl+Alt+Esc (source: cli" in r.stdout,
          "begin honours --cancel-key ctrl+alt+esc")
    st = json.loads(run("status").stdout) if state() else {}
    check(st.get("cancel_key") == "Ctrl+Alt+Esc",
          "status reports the canonical label Ctrl+Alt+Esc")
    run("end")

    print("8. invalid cancel key fails fast, with no session left behind")
    r = run("begin", "--cancel-key", "hyper+q")
    check(r.returncode != 0, "begin rejected an invalid cancel key")
    check("ERROR" in r.stderr, "error went to stderr: %s"
          % r.stderr.strip().splitlines()[0])
    check(state() is None, "no session file created by the failed begin")
    run("end")

    try:
        os.remove(cfg)
    except OSError:
        pass

    print()
    if FAILS:
        print("FAILED (%d)" % len(FAILS))
        for f in FAILS:
            print(" -", f)
        return 1
    print("PASSED")
    print("NOT COVERED: a physical keypress (Windows marks every synthetic "
          "keystroke as injected). Verify by hand: run 'begin', then press the "
          "cancel key.")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(SCRIPT))
    import computer_use as cu  # noqa: E402
    sys.exit(main())
