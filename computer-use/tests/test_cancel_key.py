"""Gate tests for the cancel-key setting: parser, precedence, classification.

Fast, deterministic, no daemon, no keystrokes. Run:

    python -m unittest discover -s tests -v
"""
import ctypes
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "scripts", "computer_use.py")
sys.path.insert(0, os.path.dirname(SCRIPT))

import computer_use as cu  # noqa: E402


class CancelKeyParse(unittest.TestCase):
    """The default is ESC, and every spelling of it means the same key."""

    def test_default_is_escape(self):
        spec = cu.parse_cancel_key(cu.DEFAULT_CANCEL_KEY)
        self.assertEqual(spec.vk, 0x1B)
        self.assertEqual(spec.mods, 0)
        self.assertEqual(spec.label, "Esc")

    def test_escape_spellings_are_equivalent(self):
        for text in ("esc", "ESC", "Esc", "escape", "  eSc  "):
            self.assertEqual(cu.parse_cancel_key(text), cu.parse_cancel_key("esc"),
                             text)
            self.assertEqual(cu.parse_cancel_key(text).label, "Esc", text)

    def test_previous_default_still_parses(self):
        spec = cu.parse_cancel_key("Ctrl+Alt+Esc")
        self.assertEqual(spec.vk, 0x1B)
        self.assertEqual(spec.mods, cu.MOD_CONTROL | cu.MOD_ALT)
        self.assertEqual(spec.label, "Ctrl+Alt+Esc")

    def test_modifier_order_is_canonical(self):
        # Whatever order the user types, the label (and the stored setting) is
        # stable, so 'Ctrl+Alt+Esc' and 'alt+ctrl+esc' cannot drift apart.
        for text in ("Ctrl+Alt+Esc", "alt+ctrl+esc", "Alt+Ctrl+Esc",
                     "ctrl+alt+esc"):
            self.assertEqual(cu.parse_cancel_key(text).label, "Ctrl+Alt+Esc", text)

    def test_alias_modifiers_normalise(self):
        self.assertEqual(cu.parse_cancel_key("control+f8").label, "Ctrl+F8")
        self.assertEqual(cu.parse_cancel_key("windows+f8").label, "Win+F8")
        self.assertEqual(cu.parse_cancel_key("meta+f8").label, "Win+F8")

    def test_function_keys(self):
        self.assertEqual(cu.parse_cancel_key("f8").vk, 0x77)
        self.assertEqual(cu.parse_cancel_key("f12").vk, 0x7B)
        self.assertEqual(cu.parse_cancel_key("shift+f8").label, "Shift+F8")

    def test_unmodified_shift_is_allowed_but_shift_is_a_modifier(self):
        self.assertEqual(cu.parse_cancel_key("shift+f8").mods, cu.MOD_SHIFT)

    def test_rejected_specs(self):
        for text in ("space", "ctrl+ctrl+esc", "hyper+q", "ctrl+",
                     "", "   ", "ctrl+alt", "esc+f8", "q"):
            with self.assertRaises(ValueError, msg=text):
                cu.parse_cancel_key(text)

    def test_error_messages_are_actionable(self):
        with self.assertRaises(ValueError) as cm:
            cu.parse_cancel_key("hyper+q")
        msg = str(cm.exception)
        self.assertIn("more than one main key", msg)
        with self.assertRaises(ValueError) as cm:
            cu.parse_cancel_key("nope")
        self.assertIn("unknown key", str(cm.exception))
        self.assertIn("Esc", str(cm.exception))   # lists the valid keys

    def test_non_string_rejected(self):
        for value in (None, 42, [1]):
            with self.assertRaises(ValueError):
                cu.parse_cancel_key(value)

    def test_spec_equality_and_hash(self):
        a = cu.parse_cancel_key("esc")
        b = cu.parse_cancel_key("ESC")
        self.assertEqual(a, b)
        self.assertEqual(hash(a), hash(b))
        self.assertEqual(len({a, b}), 1)
        self.assertNotEqual(cu.parse_cancel_key("esc"),
                            cu.parse_cancel_key("ctrl+alt+esc"))
        self.assertEqual(str(a), "Esc")
        self.assertEqual(repr(a), "Esc")

    def test_label_round_trips(self):
        for text in ("esc", "f8", "ctrl+alt+esc", "shift+f8", "win+f9"):
            once = cu.parse_cancel_key(text)
            twice = cu.parse_cancel_key(once.label)
            self.assertEqual(once, twice, text)
            self.assertEqual(once.label, twice.label, text)

    def test_shift_and_ctrl_do_not_collide(self):
        # MOD_SHIFT=0x4 and MOD_CONTROL=0x2: a truthiness bug would merge them.
        self.assertNotEqual(cu.parse_cancel_key("shift+f8"),
                            cu.parse_cancel_key("ctrl+f8"))


class ConfigPrecedence(unittest.TestCase):
    """CLI > env > DSH config file > default, and an invalid value is loud."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cu-cfg-")
        self.cfg = os.path.join(self.tmp, "computer-use.json")
        self._env = {}
        for k in ("COMPUTER_USE_CONFIG", "COMPUTER_USE_CANCEL_KEY",
                  "COMPUTER_USE_HOME"):
            self._env[k] = os.environ.pop(k, None)
        os.environ["COMPUTER_USE_CONFIG"] = self.cfg

    def tearDown(self):
        for k, v in self._env.items():
            os.environ.pop(k, None)
            if v is not None:
                os.environ[k] = v
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_default_when_nothing_set(self):
        spec, source = cu.resolve_cancel_key()
        self.assertEqual(spec.label, "Esc")
        self.assertEqual(source, "default")

    def test_config_file_is_used(self):
        cu._write_config({"cancel_key": "F8"})
        spec, source = cu.resolve_cancel_key()
        self.assertEqual((spec.label, source), ("F8", "config"))

    def test_env_beats_config(self):
        cu._write_config({"cancel_key": "F8"})
        os.environ["COMPUTER_USE_CANCEL_KEY"] = "ctrl+alt+esc"
        spec, source = cu.resolve_cancel_key()
        self.assertEqual((spec.label, source), ("Ctrl+Alt+Esc", "env"))

    def test_cli_beats_env_and_config(self):
        cu._write_config({"cancel_key": "F8"})
        os.environ["COMPUTER_USE_CANCEL_KEY"] = "shift+f8"
        spec, source = cu.resolve_cancel_key("esc")
        self.assertEqual((spec.label, source), ("Esc", "cli"))

    def test_invalid_value_is_an_error_even_with_lower_precedence_valid(self):
        # Silently falling through would hand the user a cancel key they never
        # chose, which is the one thing this setting must never do.
        os.environ["COMPUTER_USE_CANCEL_KEY"] = "not-a-key"
        with self.assertRaises(ValueError):
            cu.resolve_cancel_key()

    def test_blank_values_are_ignored(self):
        os.environ["COMPUTER_USE_CANCEL_KEY"] = "   "
        cu._write_config({"cancel_key": ""})
        spec, source = cu.resolve_cancel_key()
        self.assertEqual((spec.label, source), ("Esc", "default"))

    def test_config_file_is_stable_text(self):
        cu._write_config({"cancel_key": "Ctrl+Alt+Esc"})
        with open(self.cfg, encoding="utf-8") as f:
            text = f.read()
        self.assertIn('"cancel_key": "Ctrl+Alt+Esc"', text)
        self.assertNotIn(".tmp", text)
        self.assertFalse(os.path.exists(self.cfg + ".tmp"))

    def test_corrupt_config_does_not_crash_startup(self):
        with open(self.cfg, "w", encoding="utf-8") as f:
            f.write("{not json")
        spec, source = cu.resolve_cancel_key()
        self.assertEqual((spec.label, source), ("Esc", "default"))

    def test_config_keeps_unrelated_keys(self):
        cu._write_config({"something_else": 1})
        cfg = cu._read_config()
        cfg["cancel_key"] = "F8"
        cu._write_config(cfg)
        self.assertEqual(cu._read_config(),
                         {"something_else": 1, "cancel_key": "F8"})

    def test_config_path_defaults_under_dsh(self):
        os.environ.pop("COMPUTER_USE_CONFIG", None)
        os.environ["COMPUTER_USE_HOME"] = self.tmp
        self.assertEqual(cu.config_path(), os.path.join(self.tmp,
                                                       "computer-use.json"))
        os.environ.pop("COMPUTER_USE_HOME", None)
        self.assertTrue(cu.config_path().endswith(
            os.path.join(".dsh", "computer-use.json")))


class HookClassification(unittest.TestCase):
    """The callback's decision, exercised without any real keyboard input.

    A KBDLLHOOKSTRUCT must be kept in a named local: addressof() of a temporary
    lets CPython free it before the callback reads it, and the test then checks
    a garbage struct (this bit the first version of the suite).
    """

    def setUp(self):
        self.fired = []
        self.spec = cu.parse_cancel_key("esc")
        self.hook = cu.CancelKeyHook(self.spec, lambda: self.fired.append(1))

    def _press(self, vk, flags=0, msg=None, ncode=0):
        kb = cu.KBDLLHOOKSTRUCT(vk, 0, flags, 0, 0)
        self.hook._callback(ncode, cu.WM_SYSKEYDOWN if msg is None else msg,
                            ctypes.addressof(kb))
        return kb

    def test_physical_escape_cancels(self):
        self._press(0x1B)
        self.assertEqual(self.fired, [1])

    def test_injected_escape_does_not_cancel(self):
        # This is the rule that stops the agent cancelling itself with its own
        # `key esc` command.
        self._press(0x1B, cu.LLKHF_INJECTED)
        self.assertEqual(self.fired, [])

    def test_other_keys_do_not_cancel(self):
        for vk in (0x41, 0x1B + 1, 0x1A, 0x0D):
            self._press(vk)
        self.assertEqual(self.fired, [])

    def test_key_up_does_not_cancel(self):
        self._press(0x1B, msg=cu.WM_SYSKEYUP)
        self._press(0x1B, msg=cu.WM_KEYUP)
        self.assertEqual(self.fired, [])

    def test_negative_hook_code_is_ignored(self):
        self._press(0x1B, ncode=-1)
        self.assertEqual(self.fired, [])

    def test_modifier_combo_needs_the_modifier_down(self):
        combo = cu.CancelKeyHook(cu.parse_cancel_key("ctrl+alt+esc"),
                                 lambda: self.fired.append(1))
        kb = cu.KBDLLHOOKSTRUCT(0x1B, 0, 0, 0, 0)
        lp = ctypes.addressof(kb)
        combo._callback(0, cu.WM_SYSKEYDOWN, lp)
        # Whatever this machine's real modifier state is, the label must agree
        # with the decision: fired exactly when Ctrl+Alt are physically down.
        held = bool(cu.user32.GetAsyncKeyState(cu.VK_CONTROL_) & cu.KEY_PRESSED
                    and cu.user32.GetAsyncKeyState(cu.VK_ALT_) & cu.KEY_PRESSED)
        self.assertEqual(self.fired, [1] if held else [])

    def test_callback_never_raises(self):
        # A raising hook callback gets silently unhooked by Windows, leaving the
        # user with no cancel key at all.
        hook = cu.CancelKeyHook(self.spec, lambda: 1 / 0)
        kb = cu.KBDLLHOOKSTRUCT(0x1B, 0, 0, 0, 0)
        hook._callback(0, cu.WM_SYSKEYDOWN, ctypes.addressof(kb))  # no raise

    def test_spec_is_held_on_the_hook(self):
        self.assertIs(self.hook.spec, self.spec)


class SessionState(unittest.TestCase):

    def test_session_records_the_cancel_key(self):
        s = cu._session_state("tok", cancel_key=cu.parse_cancel_key("F8"),
                              cancel_key_source="config")
        self.assertEqual(s["cancel_key"], "F8")
        self.assertEqual(s["cancel_key_source"], "config")
        self.assertEqual(s["hotkey"], "F8")   # legacy field kept in sync

    def test_session_cancel_key_falls_back_to_the_legacy_field(self):
        self.assertEqual(cu._session_cancel_key({"hotkey": "Ctrl+Alt+Esc"}),
                         cu.parse_cancel_key("ctrl+alt+esc"))
        self.assertEqual(cu._session_cancel_key({}),
                         cu.parse_cancel_key("esc"))
        self.assertEqual(cu._session_cancel_key({"cancel_key": "garbage"}),
                         cu.parse_cancel_key("esc"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
