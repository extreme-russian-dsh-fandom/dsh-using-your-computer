# dsh-using-your-computer

A DeepSeek Harness plugin and skill that lets an agent drive the real Windows
desktop — screenshot, mouse, keyboard, clicks, drag, scroll, typing — the way
Codex's "using your computer" does, with a calm **island indicator** on every
monitor, a **guaranteed cancel key** (ESC by default), and **minimal inter-event
delay**.

## Install

```text
dsh plugin --profile desktop add github:extreme-russian-dsh-fandom/dsh-using-your-computer
```

Verified on a clean profile:

```text
+ dsh-computer-use 0.1.0
Done in 3s using pnpm v11.28.5
```

Then add `dsh-computer-use` to the profile's `package.json` `dsh.profile.bundles`
list (alongside the other bundles) and restart DSH. Verify:

```text
dsh plugin --profile desktop why dsh-computer-use
```

Pin a tag or commit for a reproducible install:

```text
dsh plugin --profile desktop add github:extreme-russian-dsh-fandom/dsh-using-your-computer#v0.1.0
```

`dsh plugin` forwards its arguments to pnpm inside the profile directory, so any
spec pnpm accepts works. Installed plugins land in
`~/.dsh/profiles/<profile>/node_modules/`.

This package has no build step, so pnpm never runs a `prepare` script for it. If
a future version adds one, pnpm blocks it until you allow it: dsh prints the exact
key, add it under `allowBuilds` in
`~/.dsh/profiles/<profile>/pnpm-workspace.yaml`, then re-run the install.

### Engine interpreter

The plugin runs `scripts/computer_use.py` with `python` from PATH by default.
Point it at the DSH runtime's interpreter (which has Pillow) when PATH does not:

```text
setx COMPUTER_USE_PYTHON "C:\path\to\python.exe"
```

## Requirements

- Windows (the engine uses Win32 via ctypes).
- Python 3 with Pillow (already bundled in the DSH runtime). Nothing else.

## Use

The plugin registers one tool, `computer_use`, whose `args` array is the engine
command line:

```text
begin                          # take control: island on every monitor + cancel key
screenshot --out shot.png      # see the screen
click 812 540                  # flash the target box, move, click
type --text "hello"            # layout-independent Unicode typing
key "ctrl+c"                   # combos or named keys
end                            # release control
```

The agent-facing instructions are in [SKILL.md](SKILL.md).

### Without the plugin (skill only)

Copy `SKILL.md` and `scripts/` into `~/.dsh/ecc/skills/computer-use/` and drive
the engine directly with the commands below. Everything works; you just call the
engine yourself instead of through the `computer_use` tool.

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

Cancel is a `WH_KEYBOARD_LL` hook, not `RegisterHotKey`: Windows reserves a bare
ESC for hotkeys, and `RegisterHotKey` cannot report which key fired. Injected
events are ignored, so an agent `key esc` command can never cancel its own
session.

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
| `COMPUTER_USE_PYTHON` | `python` | Interpreter the plugin runs the engine with. |
| `COMPUTER_USE_CANCEL_KEY` | unset | Cancel key for this process; beats the config file, loses to `begin --cancel-key`. |
| `COMPUTER_USE_CONFIG` | `~/.dsh/computer-use.json` | Full path to the DSH setting file. |
| `COMPUTER_USE_HOME` | `~/.dsh` | Directory the setting file lives in. |
| `COMPUTER_USE_COLOR` | `FF8C1A` | Accent colour (RGB hex) — orange by default. |
| `COMPUTER_USE_ALPHA` | `210` | Target-box opacity (0–255). |
| `COMPUTER_USE_DELAY_MS` | `10` | Inter-event floor (ms). |
| `COMPUTER_USE_LINGER_MS` | `150` | Target-box linger (ms). |
| `COMPUTER_USE_TIMEOUT_MS` | `120000` | Plugin-side tool timeout (ms). |

## Layout

```
├── package.json              # plugin manifest (dsh.bundle -> patch)
├── cordis.patch.yml          # registers the computer_use tool
├── index.js                  # Host plugin: runs the engine as a subprocess
├── .gitignore
├── SKILL.md                  # agent instructions (the skill)
├── README.md                 # this file
├── PRIOR_ART.md              # 25 years of prior art, synthesized
├── scripts/
│   └── computer_use.py       # zero-dep engine (ctypes + Pillow only)
└── tests/
    ├── test_cancel_key.py      # gate tests: parser, precedence, hook logic
    └── test_cancel_key_e2e.py  # live-daemon end-to-end checks
```

## License

MIT

### Plugin activation error

If enabling the plugin reports `timeoutMs must be an integer between 5000 and
1800000`, update the installed plugin to the latest `main` revision and restart
DSH. Older bundle patches left a JavaScript ternary unquoted; YAML interpreted
its `: ` as a mapping, passing an object where the plugin expected a number.
The patch now quotes JavaScript expressions. Numeric timeout strings are also
accepted; values must remain between 5000 and 1800000 milliseconds.

The activation regression checks run with `npm test`. They parse the actual
bundle patch, verify tool registration and the engine path, cover overrides and
invalid values, and reproduce the old unquoted expression failure.
