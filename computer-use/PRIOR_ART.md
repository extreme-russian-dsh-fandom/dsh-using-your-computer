# Prior Art: who solved desktop automation for decades, and what they'd tell us

A research synthesis for the **computer-use** skill: what the people who shipped
mouse/keyboard/screen automation to production over the last 25 years learned,
their mistakes, how their views evolved, and — through their eyes — what our
skill should do and why. Claims are cited where a source was fetched; inferences
are marked **[inference]**.

---

## 1. AutoIt (1999) and AutoHotkey (2003)

**Who.** AutoIt was started by Jonathan Bennett (UK) in late 1998 to escape a
painful Windows NT 4 build-automation job where installers had no silent mode
and the existing tool (ScriptIt) was "extremely unreliable… fussy about the
speed of the machine… no way to control the key delays between keystrokes."
AutoHotkey was created by Chris Mallett, released November 2003, as a hotkey/
hotstring-focused fork of AutoIt v2. Both are still in production 25 years on.

**What they learned:**

- **The very first feature was the controllable key delay.** Bennett's whole
  motivation was "I could then control the delay." The OS and the target app
  need real wall-clock time to process each injected event; hammering faster
  than the target redraws drops inputs. The answer is *variable, event-aware*
  delay, not zero — and not a blind `Sleep`.
- **`SendInput` is the only reliable injection path.** AutoHotkey's docs are the
  25-year consensus: `SendInput` "is the fastest and generally the most reliable
  method." `SendEvent` obeys a ~10 ms default delay and "will never match the
  speed of SendInput"; `SendPlay` is deprecated and "may have no effect at all"
  on Windows 11. On Windows today you build around `SendInput`.
- **SendInput has hard limits:** the OS caps a batch at ~5000 characters; it
  ignores per-key delays; and if another process has a low-level hook, it
  silently falls back to the slow path. Chunk long payloads, and don't rely on
  intra-batch timing.
- **Focus is a first-class primitive.** Injected input goes to whatever has
  foreground focus, so "activate, then verify activated, then type" is
  mandatory. Activation is asynchronous — hence a settle-after-activate delay.
- **A global hotkey that always aborts is the control-return mechanism**, and it
  must be registered and known-good *before* any injection. Critically,
  AutoHotkey uninstalls its own low-level hook mid-send for speed — so the abort
  watcher must live on a **separate thread/process** from the injection loop.
- **Scope discipline.** Bennett's own words: the "if someone asked for a
  function I stuck it in" phase left the source "a total mess," twice. A small,
  stable core API is what lets a 25-year-old tool survive. → Keep a minimal
  action surface.

## 2. Robotic Process Automation — UiPath, Blue Prism, Automation Anywhere

**Who.** Blue Prism (2001) coined "RPA." UiPath started 2005 in Bucharest as an
outsourcing shop (Daniel Dines) and productized its in-house tooling after 2008;
it IPO'd in 2021. This is the billion-dollar lineage.

**The defining evolution: coordinates → selectors.** The industry pivoted away
from screen-scraping and raw `(x, y)` clicks toward selector-based automation —
each element identified by a stable queryable descriptor, not by where it sits.
Why coordinate clicking is fragile: (1) **layout drift** (reflow, resolution,
toolbars, localization — the pixel that was the button is whitespace today);
(2) **DPI/scaling** (same logical coordinate = different physical pixels);
(3) **resolution/monitor/RDP changes**; (4) **timing/occlusion** (a coordinate
click has no precondition check that the element exists and is visible).

**The three-tier input ladder (UiPath's own docs):** *Hardware Events*
(`SendInput`, real foreground input, reliable but needs visibility) →
*SendWindowMessages* (WM_* to the control, background-capable, Win32-only) →
*Simulate* (the target's native object model — Chromium DevTools, Java/.NET API —
fastest, no focus, app-specific). Try the ladder top-down; fall to `SendInput`
only when the deterministic layers are absent.

**Attended vs unattended.** Attended = human present, sharing the machine.
That is our model: we can assume foreground/visible, the human is our kill
switch, but we must tolerate the human's own input interleaving with ours.

**Audit, recovery, human-in-the-loop.** RPA is sold on traceability ("who did
what, when, with what data") more than speed. Standard practice: per-action
audit logs, retry/transaction recovery, human approval gates for consequential
steps — and lint rules that *ban hardcoded delays* (`UI-PRR-004`: replace
`Sleep(5000)` with wait-for-element).

## 3. Microsoft MSAA (1995) → UI Automation (2005)

**What it is.** MSAA shipped ~Windows 95 for screen readers; UI Automation (UIA)
replaced it ~2005 and is now the canonical Windows accessibility/automation
surface. It exposes every control as a node with stable properties
(`ControlType`, `Name`, `AutomationId`, `ClassName`) and patterns (Invoke,
Value, Text, Selection).

**Why the element tree beats pixels:** *identity over geometry* (invoke "Save"
wherever it is); *preconditions* (find it, read `IsEnabled`, then act — a
check-then-act guarantee pixels can't give); *read-back* (verify the field now
contains the text, the dialog closed); *no focus requirement* (Invoke/SetValue
act directly, eliminating the "wrong window has focus" class of bugs).

**Limits (why pixels still exist):** self-drawn/owner-drawn controls, games and
GPU-rendered apps, canvas-heavy web content, and RDP/Citrix (where the remote UI
is a bitmap and the local UIA tree is empty — the documented reason UiPath
selectors fail over Citrix and must fall back to image matching).

Raymond Chen's own 2025 answer: use UI Automation to drive the target program;
if that's insufficient, use `SendInput`.

## 4. Sikuli (2009, MIT CSAIL)

Tom Yeh, Tsung-Hsiang Chang, Robert Miller (UIST '09): use a *screenshot* of the
target as the search query. It validated the LLM-computer-use premise two
decades early — vision over pixels can drive arbitrary GUI automation with zero
app knowledge. But its documented failure modes are the pixel-grounding warning:
DPI/resolution scaling (the most common failure), theme/appearance changes,
occlusion, and sub-pixel rendering differences that force loose thresholds and
false positives. Pixels are the universal fallback and the least stable
grounding.

## 5. Modern LLM "Computer Use" — OpenAI Operator & Anthropic (2024–2025)

- **Coordinate grounding.** The LLM reasons in screenshot-pixel space; input is
  injected in physical-pixel space. They differ under DPI scaling, resolution
  downsampling, or VM virtualization — the single most common bug. The fix: make
  screenshot pixels == injection pixels, and report the display dimensions so
  the space is self-describing.
- **Action schema.** Anthropic's primitives are the model to copy:
  `mouse_move`, `left_click`, `left_click_drag`, `right_click`, `middle_click`,
  `double_click`, `scroll`, `key`, `type`, `cursor_position`, `screenshot`.
  Primitives, not composite verbs, so the harness can interrupt *between* them.
- **Highlight = trust.** The human must see exactly what is about to be clicked.
  Two hard requirements: the indicator must be **click-through** and **must not
  steal focus** (a focus-stealing overlay breaks foreground injection).
- **Safety.** Operator documents *watch / confirm / take-over* modes, an
  allowlist, sensitive-action confirmation, and the human taking over at any
  moment. The invariant: **the human's abort is the ground-truth safety
  mechanism and must be unreachable by the model's own tooling** — a separate
  privileged path, never another model-callable action.
- **Injected-input detection.** Synthetic input carries the `LLKHF_INJECTED`
  flag and is blocked/flagged by anti-bot and some hardened apps.

## 6. Linux XTEST/xdotool vs Wayland — the security tension

XTEST let any X11 client inject input anywhere; Wayland deliberately removed it,
because any client could spoof windows, keylog, or escape sandboxes. The cost:
`xdotool` no longer works on native Wayland; accessibility had to be re-plumbed
through privileged portals. Two lessons: **input injection is a privilege and
the platform is right to gate it** (treat it as an explicitly user-granted,
audit-logged capability), and **the OS has a legitimate reason to steer you to
the accessibility layer instead of raw injection** — which on Windows exists and
is excellent (UIA). Windows' own gate is **UIPI**: an unelevated process cannot
`SendInput` to an elevated window.

---

## What they'd propose for our skill — ranked

1. **UIA as primary grounding, `SendInput`-on-pixels as fallback** (not the
   reverse). Reason on screenshots, but execute through the element tree when an
   element resolves; fall to coordinates only for self-drawn/GPU/RDP targets.
   This eliminates DPI, focus, layout-drift, and verify-by-guess at the root.
2. **Per-Monitor-V2 DPI awareness, with screenshot-pixel == injection-pixel.**
   Call `SetProcessDpiAwarenessContext(PER_MONITOR_AWARE_V2)` at startup and hold
   the invariant 1 screenshot px == 1 physical px == 1 coordinate. (If you
   downscale before sending to the model, scale coordinates back up.)
3. **Abort hotkey on a separate watchdog, registered before anything runs.**
   `RegisterHotKey(Ctrl+Alt+Esc)` is the clean path; a low-level keyboard hook on
   a dedicated thread is the guaranteed fallback. The watcher must be a separate
   process/thread from the injection loop. On abort: set a flag, stop injecting,
   clear the overlay, restore the cursor.
4. **Event-aware settling, never blind sleeps.** Batch text through `SendInput`
   (no per-key delay); after state-changing actions use a predicate wait (poll
   for screen-stable / element-appeared at ~50–100 ms) instead of `Sleep`.
5. **Atomic, interruptible primitive schema; screenshot as the result of every
   step.** Copy Anthropic's verbs, add `wait`, and report display dimensions.
6. **Click-through, non-activating overlay** (`WS_EX_TRANSPARENT |
   WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_NOACTIVATE`) drawn so it never pollutes
   the model's view — capture first, then highlight.
7. **Audit every action + confirm/take-over modes** (Operator's watch/confirm/
   take-over as a knob; hard confirmation before submit/send/delete/purchase).
8. **Respect UIPI** — decide and document the privilege model; don't silently
   click nothing against an elevated window.

**How the shipped skill maps to this:** items 2, 3, 4 (batching + `wait
--stable`), 5, 6, 7, and 8 are implemented in `scripts/computer_use.py` and
`SKILL.md`. Item 1 (UIA-primary) is documented as the production-hardening step:
the coordinate engine is the correct Codex-parity default, and UIA is the
recommended next layer once a UIA binding (pywinauto) is available in the
environment.

---

## Sources

- AutoIt developer history — https://www.autoitscript.com/autoit3/docs/intro/dev_history.htm
- AutoHotkey SendMode — https://www.autohotkey.com/docs/v2/lib/SendMode.htm
- AutoHotkey (Wikipedia) — https://en.wikipedia.org/wiki/AutoHotkey
- Raymond Chen, "You can't simulate keyboard input with PostMessage" — https://devblogs.microsoft.com/oldnewthing/20050530-11/?p=35513
- Raymond Chen, revisited 2025 (UIA first, SendInput second, LLKHF_INJECTED) — https://devblogs.microsoft.com/oldnewthing/20250319-00/?p=110979
- UiPath UI Automation — Input methods — https://docs.uipath.com/activities/other/latest/ui-automation/input-methods
- Sequoia, "UiPath IPO: Years in the Making" — https://sequoiacap.com/article/uipath-ipo-years-in-the-making
- Blue Prism Operational Audit — https://documentation.blueprism.com/bp-7-3/en-us/z-pdf/audit/en-us/blue-prism-6-7-operational-audit-overview.pdf
- Nordflux, "Automating SAP over Citrix: why UiPath selectors fail" — https://nordflux.de/en/insights/automating-sap-over-citrix-why-uipath-selectors-fail-and-what-helps
- Microsoft UI Automation / New Accessibility Model — https://learn.microsoft.com/windows/win32/winauto/entry-uiauto-win32
- Sikuli (UIST '09) — https://dspace.mit.edu/entities/publication/604a3f79-451d-4186-a94e-d12690ffe40a
- SikuliX DPI failure — https://stackoverflow.com/questions/79485751/sikulix-fails-to-detect-images-after-resizing-for-resolution-and-dpi-scaling-adj
- OpenAI Operator System Card — https://openai.com/index/operator-system-card/
- Anthropic Computer use tool — https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool
- Anthropic best practices for computer/browser use — https://claude.com/blog/best-practices-for-computer-and-browser-use-with-claude
- Project Zero, Tor X11 sandbox breakout — https://project-zero.issues.chromium.org/issues/42450321
- Launchpad #2075962, XTest on XWayland — https://bugs.launchpad.net/ubuntu/+source/xwayland/+bug/2075962
- KDE Discuss, accessibility and Wayland — https://discuss.kde.org/t/my-accessibility-stack-and-the-future-on-wayland/47421/17
