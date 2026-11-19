# Computer Use — control the user's desktop

A DeepSeek Harness plugin and skill that lets an agent drive the real Windows
desktop — screenshot, mouse, keyboard, clicks, drag, scroll, typing — the way
Codex's "using your computer" does, with a calm **island indicator** on every
monitor, a **guaranteed cancel key** (ESC by default), and **minimal inter-event
delay**.

```
dsh-using-your-computer/
├── package.json                # the plugin manifest (dsh.bundle -> patch)
├── cordis.patch.yml            # registers the computer_use tool
├── index.js                    # Host plugin: runs the engine as a subprocess
├── .gitignore
└── computer-use/
    ├── SKILL.md                # agent instructions (the skill)
    ├── scripts/
    │   └── computer_use.py     # zero-dep engine (ctypes + Pillow only)
    ├── tests/
    │   ├── test_cancel_key.py      # gate tests: parser, precedence, hook logic
    │   └── test_cancel_key_e2e.py  # live-daemon end-to-end checks
    ├── PRIOR_ART.md            # 25 years of production lessons, synthesized
    └── README.md               # this file
```

## Install

Install the plugin into a DSH profile straight from GitHub:

```text
dsh plugin --profile desktop add github:extreme-russian-dsh-fandom/dsh-using-your-computer
```

Then add the bundle to the profile's `package.json` `dsh.profile.bundles` list
(alongside the other bundles) and restart DSH. Verify with:

```text
dsh plugin --profile desktop why dsh-computer-use
```

Pin a tag or commit for a reproducible install:

```text
dsh plugin --profile desktop add github:extreme-russian-dsh-fandom/dsh-using-your-computer#v0.1.0
```

`dsh plugin` forwards its arguments to pnpm inside the profile directory, so any
spec pnpm accepts works. Installed plugins appear under
`~/.dsh/profiles/<profile>/node_modules/`.

### Engine interpreter

The plugin runs `computer-use/scripts/computer_use.py` with `python` from PATH by
default. Point it at the DSH runtime's interpreter (which has Pillow) when PATH
does not:

```text
setx COMPUTER_USE_PYTHON "C:\path\to\python.exe"
```

## Requirements

- Windows (the engine uses Win32 via ctypes).
- Python 3 with Pillow (already bundled in the DSH runtime). Nothing else.

## Without the plugin (skill only)

Copy `computer-use/` into `~/.dsh/ecc/skills/` and drive the engine directly
with the `python scripts/computer_use.py` commands below. Everything works; you
just call the engine yourself instead of through the `computer_use` tool.

## Quickstart

```text
python scripts/computer_use.py self_test        # non-destructive diagnostics
python scripts/computer_use.py begin            # island pill on every monitor + cancel key
python scripts/computer_use.py screenshot --out s.png
python scripts/computer_use.py click 812 540
python scripts/computer_use.py type --text "hello"
python scripts/computer_use.py key "ctrl+c"
python scripts/computer_use.py end              # release control
```

**Cancel key: `ESC`** by default — works anywhere, even if the agent stalls.

## The cancel key

`ESC` out of the box. Change it in DSH:

```text
python scripts/computer_use.py config                      # show the effective key + source
python scripts/computer_use.py config --cancel-key "Ctrl+Alt+Esc"
python scripts/computer_use.py config --reset              # back to ESC
```

`SPEC` is a named key plus optional modifiers: `ESC`, `f8`, `Ctrl+Alt+Esc`,
`Shift+F8`. The stored setting lives in `~/.dsh/computer-use.json`.

Precedence, first match wins:

| # | Source | Scope |
|---|---|---|
| 1 | `begin --cancel-key SPEC` | this session |
| 2 | `COMPUTER_USE_CANCEL_KEY` | this process |
| 3 | `~/.dsh/computer-use.json` (the DSH setting) | persistent |
| 4 | `DEFAULT_CANCEL_KEY` = `ESC` | fallback |

An invalid `SPEC` fails at `begin` with an actionable error and no session
starts. A cancel key that silently did not bind is the one outcome this design
refuses to ship, so `begin` and `status` both report whether it is live
(`cancel_key_live`).

## Tests

```text
python -m unittest discover -s tests -v      # 33 gate tests, ~0.03 s
python tests/test_cancel_key_e2e.py          # live daemon, ~5 s
```

Windows marks every synthetic keystroke as injected, so no automated test can
press the key as a human would. The e2e proves the classification and the live
hook install; confirm the physical press once by hand: `begin`, press the cancel
key, `status` shows `stop_requested: true`.

## Safety guarantees

1. **Island on capture.** `begin` draws a small **island pill** at the top-center
   of **every monitor**: neutral near-black surface, one orange accent dot,
   white "Deepseek using your computer" + gray "<key> to cancel". Every
   click/drag/scroll flashes an orange box at the exact target. Click-through,
   never steals focus.
2. **Guaranteed control return.** The cancel key (`ESC` by default) aborts; the
   overlay is owned by a daemon whose death destroys its windows, so a stuck
   highlight is structurally impossible; every action re-checks the stop flag
   first.
3. **No long delays.** Moves are instant; typing is batched (no per-key delay);
   click/scroll use a ~10 ms floor; `wait --stable` replaces blind sleeps.

## Colour

The island uses a calm neutral palette instead of a high-chroma fill: near-black
surface `#121214`, off-white text `#F2F2F7` (~16:1 contrast, WCAG AAA),
secondary gray `#8E8E93`, and a single orange accent dot `#FF8C1A` (the
60-30-10 rule: mostly neutral, one small accent).

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `COMPUTER_USE_CANCEL_KEY` | unset | Cancel key for this process; beats the config file, loses to `begin --cancel-key`. |
| `COMPUTER_USE_CONFIG` | `~/.dsh/computer-use.json` | Full path to the DSH setting file. |
| `COMPUTER_USE_HOME` | `~/.dsh` | Directory the setting file lives in. |
| `COMPUTER_USE_COLOR` | `FF8C1A` | Accent colour (RGB hex) — orange by default. |
| `COMPUTER_USE_ALPHA` | `210` | Target-box opacity (0–255). |
| `COMPUTER_USE_DELAY_MS` | `10` | Inter-event floor (ms). |
| `COMPUTER_USE_LINGER_MS` | `150` | Target-box linger (ms). |

## See also

- `SKILL.md` — the full agent workflow and safety contract.
- `PRIOR_ART.md` — what AutoIt/AutoHotkey, RPA, UIA, Sikuli, and modern
  computer-use agents learned, and how this skill applies it.
