import sys
import types
import unittest


def _install_stubs() -> None:
    fake_tk = types.ModuleType("tkinter")
    fake_tk.TclError = Exception
    fake_tk.Tk = object
    fake_tk.Label = object
    sys.modules.setdefault("tkinter", fake_tk)

    fake_keyboard = types.ModuleType("keyboard")
    fake_keyboard.Key = types.SimpleNamespace(
        f8=types.SimpleNamespace(name="f8"),
        f9=types.SimpleNamespace(name="f9"),
    )
    fake_keyboard.Listener = object

    fake_pynput = types.ModuleType("pynput")
    fake_pynput.keyboard = fake_keyboard
    sys.modules.setdefault("pynput", fake_pynput)
    sys.modules.setdefault("pynput.keyboard", fake_keyboard)


_install_stubs()

import hungry_clicker


class _DummyStatus:
    def update(self, *_args, **_kwargs) -> None:
        return

    def pump(self) -> bool:
        return True


class HungryClickerBatchTests(unittest.TestCase):
    def test_burst_commands_keeps_only_roll_lines(self) -> None:
        original = hungry_clicker.ROLL_COMMANDS_TEXT
        try:
            hungry_clicker.ROLL_COMMANDS_TEXT = "/roll\n  /roll 1d6  \nhello\n,/roll 2d4\n"
            self.assertEqual(
                hungry_clicker.HungryClicker._burst_commands(),
                ["/roll", "/roll 1d6", "/roll 2d4"],
            )
        finally:
            hungry_clicker.ROLL_COMMANDS_TEXT = original

    def test_send_roll_burst_uses_millisecond_delays(self) -> None:
        clicker = hungry_clicker.HungryClicker(_DummyStatus())
        original_text = hungry_clicker.ROLL_COMMANDS_TEXT
        original_initial = hungry_clicker.BATCH_INITIAL_DELAY_MS
        original_between = hungry_clicker.BATCH_COMMAND_DELAY_MS
        original_sleep = hungry_clicker.time.sleep

        calls = []
        sleeps = []

        try:
            hungry_clicker.ROLL_COMMANDS_TEXT = "/roll 1d6,/roll 2d6,/roll"
            hungry_clicker.BATCH_INITIAL_DELAY_MS = 120
            hungry_clicker.BATCH_COMMAND_DELAY_MS = 40

            clicker._ensure_connected = lambda _page: calls.append("connected")
            clicker._scroll_to_bottom = lambda _page: calls.append("scrolled")
            clicker._submit_roll_command = lambda _page, command: calls.append(command) or True
            hungry_clicker.time.sleep = lambda seconds: sleeps.append(seconds)

            clicker._send_roll_burst(object())

            self.assertEqual(
                calls,
                ["connected", "scrolled", "/roll 1d6", "/roll 2d6", "/roll"],
            )
            self.assertEqual(sleeps, [0.12, 0.04, 0.04])
            self.assertEqual(clicker.batch_count, 1)
        finally:
            hungry_clicker.ROLL_COMMANDS_TEXT = original_text
            hungry_clicker.BATCH_INITIAL_DELAY_MS = original_initial
            hungry_clicker.BATCH_COMMAND_DELAY_MS = original_between
            hungry_clicker.time.sleep = original_sleep


if __name__ == "__main__":
    unittest.main()
