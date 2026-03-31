import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from coin_flip_heads.app import CoinFlipHeadsBot, GambleConfig, load_config, save_config, AppLogger, CoinFlipHeadsUI


class CoinFlipHeadsLogicTests(unittest.TestCase):
    def _config(self) -> GambleConfig:
        return GambleConfig(
            channel_url="https://discord.com/channels/a/b",
            bet_amount=100,
            headless=True,
            session_dir="session",
            action_timeout_ms=7000,
            round_timeout_seconds=30,
            retry_delay_seconds=1.5,
        )

    def test_determine_round_result_loss_keywords(self) -> None:
        result = CoinFlipHeadsBot._determine_round_result("You lost this round", retry_visible=False)
        self.assertEqual(result, "loss")

    def test_determine_round_result_win_keywords(self) -> None:
        result = CoinFlipHeadsBot._determine_round_result("Congrats, you win!", retry_visible=False)
        self.assertEqual(result, "win")

    def test_determine_round_result_retry_implies_loss(self) -> None:
        result = CoinFlipHeadsBot._determine_round_result("Waiting...", retry_visible=True)
        self.assertEqual(result, "loss")

    def test_extract_total_coins(self) -> None:
        coins = CoinFlipHeadsBot._extract_total_coins("Your coins: 2,774,700")
        self.assertEqual(coins, 2774700)

    def test_extract_total_coins_none_when_missing(self) -> None:
        coins = CoinFlipHeadsBot._extract_total_coins("No balance line here")
        self.assertIsNone(coins)

    def test_config_from_dict_clamps_values(self) -> None:
        cfg = GambleConfig.from_dict(
            {
                "channel_url": "u",
                "bet_amount": -3,
                "headless": 1,
                "session_dir": "s",
                "action_timeout_ms": 0,
                "round_timeout_seconds": 0,
                "retry_delay_seconds": -5,
            }
        )
        self.assertEqual(cfg.bet_amount, 1)
        self.assertEqual(cfg.action_timeout_ms, 500)
        self.assertEqual(cfg.round_timeout_seconds, 5)
        self.assertEqual(cfg.retry_delay_seconds, 0.1)

    def test_save_and_load_config_round_trip(self) -> None:
        cfg = self._config()
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            save_config(cfg, path)
            loaded = load_config(path)
        self.assertEqual(loaded, cfg)

    def test_bot_stop_request_sets_flag(self) -> None:
        bot = CoinFlipHeadsBot(self._config(), AppLogger())
        bot.request_stop()
        self.assertTrue(bot._stop.is_set())

    def test_play_round_handles_win_scenario(self) -> None:
        """Test that winning triggers 'Take coins' button click."""
        bot = CoinFlipHeadsBot(self._config(), AppLogger())
        mock_page = MagicMock()

        # Mock the helper methods to simulate a win scenario
        bot._ensure_connected = MagicMock()
        bot._scroll_to_bottom = MagicMock()
        bot._send_gamble_command = MagicMock()
        bot._click_button = MagicMock(return_value=True)
        bot._fill_bet = MagicMock()
        bot._wait_for_round_result = MagicMock(return_value=("win", 5000))

        # Execute the round
        bot._play_round(mock_page)

        # Verify that _click_button was called with "Take coins" related labels
        calls = bot._click_button.call_args_list
        take_coins_call = None
        for call in calls:
            if "Take coins" in call[0][1] or "Take Coins" in call[0][1]:
                take_coins_call = call
                break

        self.assertIsNotNone(take_coins_call, "Take coins button should be clicked on win")

    def test_play_round_handles_loss_scenario(self) -> None:
        """Test that losing triggers 'Retry' button click."""
        bot = CoinFlipHeadsBot(self._config(), AppLogger())
        mock_page = MagicMock()

        # Mock the helper methods to simulate a loss scenario
        bot._ensure_connected = MagicMock()
        bot._scroll_to_bottom = MagicMock()
        bot._send_gamble_command = MagicMock()
        bot._click_button = MagicMock(return_value=True)
        bot._fill_bet = MagicMock()
        bot._wait_for_round_result = MagicMock(return_value=("loss", 4900))

        # Execute the round
        bot._play_round(mock_page)

        # Verify that _click_button was called with "Retry" label
        calls = bot._click_button.call_args_list
        retry_call = None
        for call in calls:
            if "Retry" in call[0][1]:
                retry_call = call
                break

        self.assertIsNotNone(retry_call, "Retry button should be clicked on loss")

    def test_send_gamble_command_invoked_each_round(self) -> None:
        """Test that /gamble command is sent at the start of each round."""
        bot = CoinFlipHeadsBot(self._config(), AppLogger())
        mock_page = MagicMock()

        # Mock the helper methods
        bot._ensure_connected = MagicMock()
        bot._scroll_to_bottom = MagicMock()
        bot._send_gamble_command = MagicMock()
        bot._click_button = MagicMock(return_value=True)
        bot._fill_bet = MagicMock()
        bot._wait_for_round_result = MagicMock(return_value=("win", 5000))

        # Execute the round
        bot._play_round(mock_page)

        # Verify that _send_gamble_command was called
        bot._send_gamble_command.assert_called_once_with(mock_page)

    def test_ui_sync_config_reads_timing_fields(self) -> None:
        ui = CoinFlipHeadsUI.__new__(CoinFlipHeadsUI)
        ui.config = self._config()
        ui.url_var = MagicMock()
        ui.bet_var = MagicMock()
        ui.headless_var = MagicMock()
        ui.action_timeout_var = MagicMock()
        ui.round_timeout_var = MagicMock()
        ui.retry_delay_var = MagicMock()

        ui.url_var.get.return_value = "https://discord.com/channels/new/url"
        ui.bet_var.get.return_value = "250"
        ui.headless_var.get.return_value = False
        ui.action_timeout_var.get.return_value = "9000"
        ui.round_timeout_var.get.return_value = "45"
        ui.retry_delay_var.get.return_value = "2.2"

        ui._sync_config_from_fields()

        self.assertEqual(ui.config.channel_url, "https://discord.com/channels/new/url")
        self.assertEqual(ui.config.bet_amount, 250)
        self.assertEqual(ui.config.action_timeout_ms, 9000)
        self.assertEqual(ui.config.round_timeout_seconds, 45)
        self.assertEqual(ui.config.retry_delay_seconds, 2.2)

    def test_ui_sync_config_rejects_low_action_timeout(self) -> None:
        ui = CoinFlipHeadsUI.__new__(CoinFlipHeadsUI)
        ui.config = self._config()
        ui.url_var = MagicMock()
        ui.bet_var = MagicMock()
        ui.headless_var = MagicMock()
        ui.action_timeout_var = MagicMock()
        ui.round_timeout_var = MagicMock()
        ui.retry_delay_var = MagicMock()

        ui.url_var.get.return_value = "https://discord.com/channels/new/url"
        ui.bet_var.get.return_value = "100"
        ui.headless_var.get.return_value = True
        ui.action_timeout_var.get.return_value = "499"
        ui.round_timeout_var.get.return_value = "30"
        ui.retry_delay_var.get.return_value = "1.5"

        with self.assertRaises(ValueError):
            ui._sync_config_from_fields()


if __name__ == "__main__":
    unittest.main()
