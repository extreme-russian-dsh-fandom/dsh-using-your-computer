# dsh-using-your-computer

A DeepSeek Harness plugin that lets the agent drive the user's Windows desktop —
screenshot, mouse, keyboard, clicks, drag, scroll, typing — the way Codex's
"using your computer" does.

While control is held the user sees a calm **island indicator on every monitor**,
and pressing the **cancel key (ESC by default)** always takes control back, even
if the agent stalls.

## Install

```text
dsh plugin --profile desktop add github:extreme-russian-dsh-fandom/dsh-using-your-computer
```

Add `dsh-computer-use` to the profile's `package.json` `dsh.profile.bundles`
list, then restart DSH. Verify:

```text
dsh plugin --profile desktop why dsh-computer-use
```

The engine needs Python with Pillow. The DSH runtime already ships one; if
`python` on PATH is not it, set `COMPUTER_USE_PYTHON` to the right interpreter.

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

The agent-facing instructions live in [computer-use/SKILL.md](computer-use/SKILL.md).
The full command table, safety contract and colour notes are in
[computer-use/README.md](computer-use/README.md).

### Cancel key

`ESC` out of the box. Change it persistently in DSH:

```text
config --cancel-key "Ctrl+Alt+Esc"
config --reset                 # back to ESC
```

Stored in `~/.dsh/computer-use.json`. Precedence: `begin --cancel-key` >
`COMPUTER_USE_CANCEL_KEY` > that file > `ESC`.

Cancel is a `WH_KEYBOARD_LL` hook, not `RegisterHotKey`: Windows reserves a bare
ESC for hotkeys, and `RegisterHotKey` cannot report which key fired. Injected
events are ignored, so an agent `key esc` command can never cancel its own
session.

## Tests

```text
python -m unittest discover -s computer-use/tests -v   # 33 gate tests, fast
python computer-use/tests/test_cancel_key_e2e.py       # live daemon
```

Windows marks every synthetic keystroke as injected, so no automated test can
press the key as a human would. Confirm the physical press once by hand: call
`begin`, press ESC, check that `status` reports `stop_requested: true`.

## Layout

```
├── package.json              # plugin manifest (dsh.bundle -> patch)
├── cordis.patch.yml          # registers the computer_use tool
├── index.js                  # Host plugin: runs the engine as a subprocess
├── .gitignore
└── computer-use/
    ├── SKILL.md              # agent instructions
    ├── scripts/
    │   └── computer_use.py   # zero-dep engine (ctypes + Pillow only)
    ├── tests/                # gate tests + live-daemon e2e
    ├── PRIOR_ART.md          # 25 years of prior art, synthesized
    └── README.md
```

## License

MIT
