import sys
import types
import unittest
from unittest.mock import MagicMock, patch


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

        calls = []
        sleeps = []

        try:
            hungry_clicker.ROLL_COMMANDS_TEXT = "/roll 1d6,/roll 2d6,/roll"
            hungry_clicker.BATCH_INITIAL_DELAY_MS = 120
            hungry_clicker.BATCH_COMMAND_DELAY_MS = 40

            with patch.object(clicker, "_ensure_connected", side_effect=lambda _page: calls.append("connected")), patch.object(
                clicker, "_scroll_to_bottom", side_effect=lambda _page: calls.append("scrolled")
            ), patch.object(
                clicker, "_submit_roll_command", side_effect=lambda _page, command: calls.append(command) or True
            ), patch(
                "hungry_clicker.time.sleep", side_effect=lambda seconds: sleeps.append(seconds)
            ):
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


class SubmitRollCommandTests(unittest.TestCase):
    def _make_page(self, autocomplete_visible: bool) -> MagicMock:
        """Build a minimal page mock for _submit_roll_command."""
        page = MagicMock()

        composer = MagicMock()
        composer.wait_for.return_value = None
        composer.click.return_value = None

        locator_map: dict = {}

        autocomplete = MagicMock()
        if autocomplete_visible:
            autocomplete.wait_for.return_value = None
        else:
            autocomplete.wait_for.side_effect = hungry_clicker.PwTimeout("timeout")

        def _locator(selector: str) -> MagicMock:
            loc = MagicMock()
            loc.first = composer if "textbox" in selector else autocomplete
            return loc

        page.locator.side_effect = _locator
        page.keyboard = MagicMock()
        return page

    def test_tab_pressed_when_autocomplete_appears(self) -> None:
        page = self._make_page(autocomplete_visible=True)
        result = hungry_clicker.HungryClicker._submit_roll_command(page, "/roll")
        self.assertTrue(result)
        key_calls = [call.args[0] for call in page.keyboard.press.call_args_list]
        self.assertIn("Tab", key_calls)
        self.assertIn("Enter", key_calls)
        self.assertLess(key_calls.index("Tab"), key_calls.index("Enter"))

    def test_enter_sent_even_without_autocomplete(self) -> None:
        page = self._make_page(autocomplete_visible=False)
        result = hungry_clicker.HungryClicker._submit_roll_command(page, "/roll")
        self.assertTrue(result)
        key_calls = [call.args[0] for call in page.keyboard.press.call_args_list]
        self.assertNotIn("Tab", key_calls)
        self.assertIn("Enter", key_calls)


class TryClickRollTests(unittest.TestCase):
    def _make_clicker(self) -> hungry_clicker.HungryClicker:
        return hungry_clicker.HungryClicker(_DummyStatus())

    def _make_page_with_buttons(self, count: int) -> MagicMock:
        """Return a page mock that has `count` visible Roll! buttons."""
        page = MagicMock()

        buttons_loc = MagicMock()
        buttons_loc.first = MagicMock()
        buttons_loc.first.wait_for.return_value = None
        buttons_loc.count.return_value = count

        def _nth(i: int) -> MagicMock:
            btn = MagicMock()
            btn.is_visible.return_value = True
            btn.click.return_value = None
            return btn

        buttons_loc.nth.side_effect = _nth

        page.locator.return_value = buttons_loc
        return page

    def test_clicks_all_visible_buttons(self) -> None:
        clicker = self._make_clicker()
        page = self._make_page_with_buttons(3)
        with patch("hungry_clicker.time.sleep"):
            result = clicker._try_click_roll(page)
        self.assertTrue(result)
        self.assertEqual(clicker.roll_count, 3)

    def test_delay_between_multiple_clicks(self) -> None:
        clicker = self._make_clicker()
        page = self._make_page_with_buttons(2)
        sleeps = []
        with patch("hungry_clicker.time.sleep", side_effect=sleeps.append):
            clicker._try_click_roll(page)
        # One delay between 2 buttons, none after the last.
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], hungry_clicker.MULTI_CLICK_DELAY)

    def test_single_button_no_extra_delay(self) -> None:
        clicker = self._make_clicker()
        page = self._make_page_with_buttons(1)
        sleeps = []
        with patch("hungry_clicker.time.sleep", side_effect=sleeps.append):
            result = clicker._try_click_roll(page)
        self.assertTrue(result)
        self.assertEqual(clicker.roll_count, 1)
        self.assertEqual(sleeps, [])

    def test_returns_false_when_no_buttons(self) -> None:
        clicker = self._make_clicker()
        page = MagicMock()

        no_buttons = MagicMock()
        no_buttons.first = MagicMock()
        no_buttons.first.wait_for.side_effect = hungry_clicker.PwTimeout("timeout")
        page.locator.return_value = no_buttons

        result = clicker._try_click_roll(page)
        self.assertFalse(result)
        self.assertEqual(clicker.roll_count, 0)


if __name__ == "__main__":
    unittest.main()
