import sys
import types
import unittest
from queue import SimpleQueue
from unittest.mock import MagicMock, patch


def _install_stubs() -> None:
    fake_tk = types.ModuleType("tkinter")
    fake_tk.TclError = Exception
    fake_tk.Tk = object
    fake_tk.Label = object
    
    # Mock Checkbutton and BooleanVar for the new gamble toggle UI
    fake_tk.Checkbutton = MagicMock()
    fake_tk.BooleanVar = MagicMock()
    
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


class _DummyMaster:
    """Minimal stand-in for HungryClickerMaster used to construct ClickerThread."""

    def __init__(self) -> None:
        self.running = True
        self.active = True
        self.gamble_enabled = False
        self.pending_bursts = [SimpleQueue()]


def _make_thread() -> hungry_clicker.ClickerThread:
    return hungry_clicker.ClickerThread(0, "https://example.com", _DummyMaster())


class ClickerThreadBatchTests(unittest.TestCase):
    def test_burst_commands_keeps_only_slash_command_lines(self) -> None:
        original = hungry_clicker.ROLL_COMMANDS_TEXT
        try:
            hungry_clicker.ROLL_COMMANDS_TEXT = "/roll\n  /gamble coinflip  \nhello\n,/roll 2d4\n"
            self.assertEqual(
                hungry_clicker.ClickerThread._burst_commands(),
                ["/roll", "/gamble coinflip", "/roll 2d4"],
            )
        finally:
            hungry_clicker.ROLL_COMMANDS_TEXT = original

    def test_send_roll_burst_uses_millisecond_delays(self) -> None:
        thread = _make_thread()
        original_text = hungry_clicker.ROLL_COMMANDS_TEXT
        original_initial = hungry_clicker.BATCH_INITIAL_DELAY_MS
        original_between = hungry_clicker.BATCH_COMMAND_DELAY_MS

        calls = []
        sleeps = []

        try:
            hungry_clicker.ROLL_COMMANDS_TEXT = "/roll 1d6,/roll 2d6,/roll"
            hungry_clicker.BATCH_INITIAL_DELAY_MS = 120
            hungry_clicker.BATCH_COMMAND_DELAY_MS = 40

            with patch.object(
                thread, "_ensure_connected", side_effect=lambda _page: calls.append("connected")
            ), patch.object(
                thread, "_scroll_to_bottom", side_effect=lambda _page: calls.append("scrolled")
            ), patch.object(
                thread,
                "_submit_roll_command",
                side_effect=lambda _page, command: calls.append(command) or True,
            ), patch(
                "hungry_clicker.time.sleep", side_effect=lambda seconds: sleeps.append(seconds)
            ):
                thread._send_roll_burst(object())

            self.assertEqual(
                calls,
                ["connected", "scrolled", "/roll 1d6", "/roll 2d6", "/roll"],
            )
            self.assertEqual(sleeps, [0.12, 0.04, 0.04])
            self.assertEqual(thread.batch_count, 1)
        finally:
            hungry_clicker.ROLL_COMMANDS_TEXT = original_text
            hungry_clicker.BATCH_INITIAL_DELAY_MS = original_initial
            hungry_clicker.BATCH_COMMAND_DELAY_MS = original_between


class SubmitRollCommandTests(unittest.TestCase):
    def _make_page(
        self,
        autocomplete_item_visible: bool,
        fallback_autocomplete_visible: bool = False,
    ) -> MagicMock:
        """Build a minimal page mock for _submit_roll_command."""
        page = MagicMock()

        composer = MagicMock()
        composer.wait_for.return_value = None
        composer.click.return_value = None

        # Autocomplete item (primary path – direct click)
        autocomplete_item = MagicMock()
        if autocomplete_item_visible:
            autocomplete_item.wait_for.return_value = None
            autocomplete_item.click.return_value = None
        else:
            autocomplete_item.wait_for.side_effect = hungry_clicker.PwTimeout("timeout")

        # Fallback autocomplete container (Tab path)
        autocomplete_container = MagicMock()
        if fallback_autocomplete_visible:
            autocomplete_container.wait_for.return_value = None
        else:
            autocomplete_container.wait_for.side_effect = hungry_clicker.PwTimeout("timeout")

        def _locator(selector: str) -> MagicMock:
            loc = MagicMock()
            if "textbox" in selector:
                loc.first = composer
            elif 'role="button"' in selector or 'role="option"' in selector:
                # autocomplete item locator – caller uses .filter(...).first
                filtered = MagicMock()
                filtered.first = autocomplete_item
                loc.filter.return_value = filtered
            else:
                loc.first = autocomplete_container
            return loc

        page.locator.side_effect = _locator
        page.keyboard = MagicMock()
        return page

    def test_autocomplete_item_clicked_when_visible(self) -> None:
        """Primary path: the specific autocomplete item is clicked; Tab is NOT pressed."""
        page = self._make_page(autocomplete_item_visible=True)
        thread = _make_thread()
        result = thread._submit_roll_command(page, "/roll")
        self.assertTrue(result)
        key_calls = [call.args[0] for call in page.keyboard.press.call_args_list]
        self.assertNotIn("Tab", key_calls)
        self.assertIn("Enter", key_calls)

    def test_sleep_before_enter_in_primary_path(self) -> None:
        """Primary path: a small sleep is inserted after the autocomplete click and before Enter."""
        page = self._make_page(autocomplete_item_visible=True)
        thread = _make_thread()

        call_order: list[str] = []

        # Retrieve the autocomplete item mock that _make_page wired up
        autocomplete_item = page.locator(
            '[class*="autocomplete"] [role="button"],'
            ' [data-list-id="autocomplete-results"] [role="option"]'
        ).filter(has_text="/roll").first
        original_click = autocomplete_item.click
        autocomplete_item.click.side_effect = lambda: call_order.append("click")

        original_press = page.keyboard.press
        page.keyboard.press.side_effect = lambda key: call_order.append(f"press:{key}")

        sleeps: list[float] = []

        def _sleep(seconds: float) -> None:
            sleeps.append(seconds)
            call_order.append("sleep")

        with patch("hungry_clicker.time.sleep", side_effect=_sleep):
            result = thread._submit_roll_command(page, "/roll")

        self.assertTrue(result)
        self.assertIn("sleep", call_order)
        self.assertAlmostEqual(sleeps[-1], 0.1)
        # Verify the sleep comes after the click and before Enter
        click_idx = call_order.index("click")
        sleep_idx = call_order.index("sleep")
        enter_idx = call_order.index("press:Enter")
        self.assertLess(click_idx, sleep_idx, "sleep should come after autocomplete click")
        self.assertLess(sleep_idx, enter_idx, "sleep should come before Enter press")

    def test_tab_fallback_when_autocomplete_click_fails(self) -> None:
        """Fallback path: when the item click fails but the container is visible, use Tab+Enter."""
        page = self._make_page(
            autocomplete_item_visible=False,
            fallback_autocomplete_visible=True,
        )
        thread = _make_thread()
        result = thread._submit_roll_command(page, "/roll")
        self.assertTrue(result)
        key_calls = [call.args[0] for call in page.keyboard.press.call_args_list]
        self.assertIn("Tab", key_calls)
        self.assertIn("Enter", key_calls)
        self.assertLess(key_calls.index("Tab"), key_calls.index("Enter"))

    def test_enter_sent_even_without_autocomplete(self) -> None:
        """When no autocomplete appears at all, just Enter is sent (no Tab)."""
        page = self._make_page(
            autocomplete_item_visible=False,
            fallback_autocomplete_visible=False,
        )
        thread = _make_thread()
        result = thread._submit_roll_command(page, "/roll")
        self.assertTrue(result)
        key_calls = [call.args[0] for call in page.keyboard.press.call_args_list]
        self.assertNotIn("Tab", key_calls)
        self.assertIn("Enter", key_calls)

    def test_gamble_coinflip_types_command_and_mode(self) -> None:
        page = self._make_page(autocomplete_item_visible=True)
        thread = _make_thread()
        result = thread._submit_roll_command(page, "/gamble coinflip")
        self.assertTrue(result)
        typed = [call.args[0] for call in page.keyboard.type.call_args_list]
        self.assertIn("/gamble", typed)
        self.assertIn(" coinflip", typed)

    def test_gamble_defaults_to_coinflip_when_no_mode_provided(self) -> None:
        page = self._make_page(autocomplete_item_visible=True)
        thread = _make_thread()
        result = thread._submit_roll_command(page, "/gamble")
        self.assertTrue(result)
        typed = [call.args[0] for call in page.keyboard.type.call_args_list]
        self.assertIn("/gamble", typed)
        self.assertIn(" coinflip", typed)


class TryClickRollTests(unittest.TestCase):
    def _make_thread(self) -> hungry_clicker.ClickerThread:
        return _make_thread()

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

        all_buttons = MagicMock()
        all_buttons.filter.return_value = buttons_loc
        fallback_buttons = MagicMock()
        fallback_buttons.filter.return_value = buttons_loc

        def _locator(selector: str) -> MagicMock:
            if selector == "button":
                return all_buttons
            return fallback_buttons

        page.locator.side_effect = _locator
        return page

    def test_clicks_all_visible_buttons(self) -> None:
        thread = self._make_thread()
        page = self._make_page_with_buttons(3)
        with patch("hungry_clicker.time.sleep"):
            result = thread._try_click_roll(page)
        self.assertTrue(result)
        self.assertEqual(thread.roll_count, 3)

    def test_delay_between_multiple_clicks(self) -> None:
        thread = self._make_thread()
        page = self._make_page_with_buttons(2)
        sleeps = []
        with patch("hungry_clicker.time.sleep", side_effect=sleeps.append):
            thread._try_click_roll(page)
        # One delay between 2 buttons, none after the last.
        self.assertEqual(len(sleeps), 1)
        self.assertAlmostEqual(sleeps[0], hungry_clicker.MULTI_CLICK_DELAY)

    def test_single_button_no_extra_delay(self) -> None:
        thread = self._make_thread()
        page = self._make_page_with_buttons(1)
        sleeps = []
        with patch("hungry_clicker.time.sleep", side_effect=sleeps.append):
            result = thread._try_click_roll(page)
        self.assertTrue(result)
        self.assertEqual(thread.roll_count, 1)
        self.assertEqual(sleeps, [])

    def test_returns_false_when_no_buttons(self) -> None:
        thread = self._make_thread()
        page = MagicMock()

        no_buttons = MagicMock()
        no_buttons.first = MagicMock()
        no_buttons.first.wait_for.side_effect = hungry_clicker.PwTimeout("timeout")

        all_buttons = MagicMock()
        all_buttons.filter.return_value = no_buttons
        fallback_buttons = MagicMock()
        fallback_buttons.filter.return_value = no_buttons

        def _locator(selector: str) -> MagicMock:
            if selector == "button":
                return all_buttons
            return fallback_buttons

        page.locator.side_effect = _locator

        result = thread._try_click_roll(page)
        self.assertFalse(result)
        self.assertEqual(thread.roll_count, 0)

    def test_gamble_button_pattern_covers_new_gamble_labels(self) -> None:
        pattern = hungry_clicker.GAMBLE_BUTTON_TEXT_PATTERN
        self.assertIsNotNone(pattern.search("Heads - go again"))
        self.assertIsNotNone(pattern.search("Tails - go again"))
        self.assertIsNotNone(pattern.search("Play Again"))
        self.assertIsNotNone(pattern.search("Take coins"))


if __name__ == "__main__":
    unittest.main()
