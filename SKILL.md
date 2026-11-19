---
name: computer-use
description: "Control the user's Windows desktop on the user's behalf — screenshot the screen, move the mouse, click, double-click, right-click, drag, scroll, and type — the way Codex's 'using your computer' does, with a calm island indicator on every monitor when control is taken, a guaranteed cancel key (ESC by default, configurable in DSH), and minimal inter-event delay. Use when the user asks to operate their computer, drive a GUI app, click buttons, fill forms, or navigate the desktop by mouse and keyboard."
metadata:
  origin: community
---

# Computer Use (control the user's desktop)

Drive the user's real Windows desktop with mouse and keyboard, the way Codex's
"using your computer" does. The engine lives in `scripts/computer_use.py`
(resolve the path relative to this skill directory). It needs only Python
stdlib + Pillow — no pyautogui, no pywinauto, nothing to install.

## When to Activate

- The user asks you to operate their computer: "open X", "click Y", "fill in
  the form", "navigate to Z", "take over my desktop".
- A task requires driving a GUI application that has no CLI/API.
- You need to click buttons, type text, drag, scroll, or press shortcuts in a
  real desktop session.

### When NOT to Use

- A CLI, REST API, or file operation can do the job — prefer the deterministic
  path first (see `search-first`).
- Web pages where a browser-automation tool (Playwright) is available and
  sufficient.
- Anything you can do without touching the user's live input devices.

## Safety Contract (read before the first action)

These are non-negotiable; the user is watching their own screen.

1. **Always `begin` first, always `end` last.** Control must be explicitly
   acquired and explicitly released. `begin` draws an island pill at the
   top-center of every monitor; `end` clears it. If a run ends early or errors,
   still run `end`.
2. **The cancel key is `ESC` by default.** It works anywhere, even if you stop
   responding. Once the user presses it, every further action refuses to run.
   It is the user's guaranteed way to take control back — never suggest it can
   be disabled. The user can change it in DSH (`config --cancel-key`), and you
   may pass `begin --cancel-key` for one session; always use whatever
   `status.cancel_key` reports rather than assuming `ESC`.
3. **Confirm before consequential actions.** Submitting a form, sending a
   message, deleting, purchasing, or anything irreversible: take a screenshot,
   state plainly what you are about to do, and wait for the user to confirm.
4. **Respect the elevated-app boundary.** An unelevated process cannot inject
   into an elevated (admin) window (Windows UIPI). If a target is elevated, say
   so and ask how to proceed rather than silently clicking nothing.

## The Loop

Every task follows observe → act → observe. Never act blind.

```
begin                                    # acquire control (island pill on every monitor)
screenshot --out shot.png                # SEE the screen
# ... decide the next action from the image ...
click 812 540                            # highlight target, move, click
wait --stable                            # let the screen settle, then re-observe
screenshot --out shot.png                # SEE the result; verify it worked
# ... repeat ...
end                                      # release control, clear overlay
```

1. **Screenshot first.** Read the PNG (`read_image`) and reason about it.
2. **Act with one primitive.** One command per action, so the user (and the
   cancel key) can intervene between actions.
3. **Verify.** After a state-changing action (a click that opens a dialog, a
   type that triggers validation, a navigation), take a fresh screenshot and
   confirm the change happened. `wait --stable` waits until the screen stops
   changing before you look again.
4. **Release.** `end` as the last step, always.

## Commands

All are subcommands of `scripts/computer_use.py`. Coordinates are **physical
pixels**; the engine is made DPI-aware (Per-Monitor V2) automatically, so a
screenshot pixel and a click coordinate are the same number — you do not need
to do any scaling math.

| Command | What it does |
|---|---|
| `begin [--cancel-key SPEC]` | Acquire control: island pill on every monitor, install the cancel key. |
| `end` | Release control: clear overlay, remove the cancel key hook. |
| `status` | Session state (active / stop-requested / `cancel_key` / `cancel_key_live`). |
| `config [--cancel-key SPEC] [--reset]` | Read or change the DSH-wide cancel key setting. |
| `screenshot [--out f.png]` | Capture the screen to a PNG; prints `WxH path`. |
| `cursor_position` | Print the current cursor `x y`. |
| `move X Y` | Move the cursor (instant, no sleep). |
| `click X Y [--button left|right|middle] [--clicks 1|2]` | Draw the target box, move, click. |
| `drag X1 Y1 X2 Y2 [--button ...]` | Press at start, drag to end, release. |
| `scroll X Y AMOUNT [--horizontal]` | Scroll at the point; +up / -down. |
| `type --text "..."` (or `--file path`) | Type Unicode text (layout-independent). |
| `key "ctrl+c"` | Press a combo or a named key: `enter`, `tab`, `esc`, `f5`, arrows, `alt+tab`, … |
| `highlight X Y` | Preview a target box (1 s). |
| `wait --stable [--timeout ms]` | Wait until the screen stops changing. |
| `self_test` | Non-destructive diagnostics (screen, overlay, no input). |

Double-click is `click --clicks 2`; right-click is `click --button right`.

## The Island Indicator

When you take control, the user sees, at the top-center of **every monitor**:

- A small **"island" pill**: a calm neutral near-black surface with an **orange
  accent dot**, white primary text "Deepseek using your computer", and a calmer
  gray hint naming the cancel key ("Esc to cancel"). Neutral surface + one small
  accent (the 60-30-10 colour rule) instead of a large high-chroma fill, so it
  reads as normal UI, not a warning banner. White-on-near-black is ~16:1
  (WCAG AAA).
- A **transient orange box** at the exact point of every click/drag/scroll,
  shown before the action and cleared right after, so the user sees precisely
  what you are about to touch.

The overlay is click-through and never steals focus, so it annotates without
interfering with the click it marks. Override the accent with
`COMPUTER_USE_COLOR=RRGGBB` (default `FF8C1A`); the island surface/text tokens
are `ISLAND_BG` / `ISLAND_TEXT` / `ISLAND_HINT` in the script.

## Delays

The engine is built for low latency; do not add sleeps of your own.

- Cursor moves are instantaneous (`SetCursorPos`).
- Typed text is batched into a single `SendInput` call — no per-key delay.
- Click/scroll events use a ~10 ms floor (tunable via `COMPUTER_USE_DELAY_MS`),
  just enough for the OS to dispatch, never a 100 ms/1 s pause.
- After a *state-changing* action, use `wait --stable` (a predicate wait) to
  let the target redraw, instead of `Sleep`. Polling beats sleeping.

## Returning Control

Control is returned three ways, and at least one always works:

1. You run `end`.
2. The user presses the **cancel key** (`ESC` by default) — the daemon tears
   down the overlay and sets a stop flag that every action checks before
   touching the device.
3. If the daemon process dies for any reason (crash, kill), Windows destroys
   its overlay windows automatically — a stuck highlight is structurally
   impossible.

Check `status` after an interruption before continuing.

### The cancel key setting

Default is `ESC`. Precedence, first match wins:

1. `begin --cancel-key SPEC` — this session only.
2. `COMPUTER_USE_CANCEL_KEY` — this process environment.
3. `~/.dsh/computer-use.json` → `{"cancel_key": "..."}` — the DSH setting,
   written with `config --cancel-key`.
4. `DEFAULT_CANCEL_KEY` (`ESC`).

`SPEC` is a named key plus optional modifiers: `ESC`, `f8`, `Ctrl+Alt+Esc`,
`Shift+F8`. Modifier order does not matter; the stored label is canonical.
`Space`, modifier-only specs, printable characters, and unknown names are
rejected at `begin` with an actionable error, because a cancel key that never
fires is worse than no cancel key. The hint text on the island always shows the
key actually in effect.

Cancel is delivered by a low-level keyboard hook (`WH_KEYBOARD_LL`), not
`RegisterHotKey`, for two reasons: Windows reserves a bare `ESC` for
`RegisterHotKey`, and `RegisterHotKey` never reports which key produced the
hotkey, so two sessions bound to one combo would fight. The hook checks the
event's `vkCode` and the physical modifier state, and **ignores any injected
event**, so an agent `key esc` command can never cancel its own session.

## Trust Modes (pick per task, default Watch)

- **Watch** — you act; the user watches (default for safe, reversible work).
- **Confirm** — for consequential steps, take a screenshot and ask before each.
- **Take Over** — the user seizes control with the cancel key; respect it.

## Audit

Every action appends a structured record to
`%TEMP%\computer_use\audit.jsonl` (timestamp, action, coordinates, result), so
"who did what, when" is always answerable. Tell the user this file exists.

## Production Hardening (what the prior art recommends)

The coordinate path above is exactly how Codex works and is the correct default
for arbitrary desktops. For production-grade reliability, the desktop-automation
industry converged on **UI Automation (UIA) as the primary grounding, with
coordinate input as the fallback**, because a stable element id survives layout
drift, DPI changes, and focus races that raw pixels do not. See `PRIOR_ART.md`
for the full synthesis and sources. When the environment provides pywinauto or a
UIA binding, prefer resolving an element to its bounding rect (then clicking
that rect center with this engine) over guessing coordinates from a screenshot:

```bash
pip install pywinauto   # optional; not required for the coordinate engine
```

## Anti-Patterns

- Never skip `begin`/`end`; never leave the orange overlay on screen.
- Never `Sleep(N)` where `wait --stable` or a fresh screenshot would do.
- Never click a coordinate you have not verified against a fresh screenshot.
- Never type secrets/passwords into a field without the user's explicit say-so,
  and never put credentials in the audit log (type into a target is logged only
  as a character count).
- Never promise to control an elevated window from an unelevated process.
