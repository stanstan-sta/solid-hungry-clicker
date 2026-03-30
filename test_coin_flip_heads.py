import json
import tempfile
import unittest
from pathlib import Path

from coin_flip_heads.app import CoinFlipHeadsBot, GambleConfig, load_config, save_config, AppLogger


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


if __name__ == "__main__":
    unittest.main()
