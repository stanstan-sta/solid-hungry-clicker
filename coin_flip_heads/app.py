#!/usr/bin/env python3
"""Standalone Coin Flip automation app (Heads strategy) for Discord casino bots."""

from __future__ import annotations

import argparse
import json
import re
import signal
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable

from playwright.sync_api import sync_playwright

try:
    import tkinter as tk
except Exception:  # pragma: no cover - handled by --no-ui mode
    tk = None


DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.json")


@dataclass
class GambleConfig:
    channel_url: str
    bet_amount: int
    headless: bool
    session_dir: str
    action_timeout_ms: int
    round_timeout_seconds: int
    retry_delay_seconds: float

    @classmethod
    def from_dict(cls, raw: dict) -> "GambleConfig":
        return cls(
            channel_url=str(raw.get("channel_url", "https://discord.com/channels/YOUR_SERVER_ID/YOUR_CHANNEL_ID")),
            bet_amount=max(1, int(raw.get("bet_amount", 100))),
            headless=bool(raw.get("headless", False)),
            session_dir=str(raw.get("session_dir", "discord_session_gamble")),
            action_timeout_ms=max(500, int(raw.get("action_timeout_ms", 7000))),
            round_timeout_seconds=max(5, int(raw.get("round_timeout_seconds", 30))),
            retry_delay_seconds=max(0.1, float(raw.get("retry_delay_seconds", 1.5))),
        )


def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> GambleConfig:
    with config_path.open("r", encoding="utf-8") as fh:
        return GambleConfig.from_dict(json.load(fh))


def save_config(config: GambleConfig, config_path: Path = DEFAULT_CONFIG_PATH) -> None:
    payload = {
        "channel_url": config.channel_url,
        "bet_amount": config.bet_amount,
        "headless": config.headless,
        "session_dir": config.session_dir,
        "action_timeout_ms": config.action_timeout_ms,
        "round_timeout_seconds": config.round_timeout_seconds,
        "retry_delay_seconds": config.retry_delay_seconds,
    }
    with config_path.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")


class AppLogger:
    def __init__(self, sink: Callable[[str], None] | None = None) -> None:
        self._sink = sink
        self._lock = threading.Lock()

    def log(self, message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {message}"
        with self._lock:
            print(line, flush=True)
            if self._sink is not None:
                self._sink(line)


class CoinFlipHeadsBot:
    def __init__(self, config: GambleConfig, logger: AppLogger) -> None:
        self.config = config
        self.logger = logger
        self._stop = threading.Event()
        self.round_count = 0

    def request_stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        self.logger.log(f"Starting automation at {self.config.channel_url}")
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=self.config.session_dir,
                headless=self.config.headless,
                args=["--disable-blink-features=AutomationControlled"],
                ignore_default_args=["--enable-automation"],
                viewport={"width": 1280, "height": 900},
            )
            page = context.pages[0] if context.pages else context.new_page()

            try:
                page.goto(self.config.channel_url, wait_until="domcontentloaded", timeout=30_000)
                self.logger.log("Browser ready. Running Heads strategy loop.")
                while not self._stop.is_set():
                    try:
                        self._play_round(page)
                    except Exception as exc:  # noqa: BLE001
                        self.logger.log(
                            f"Round failed: {type(exc).__name__}: {exc}"
                        )
                        self._interruptible_sleep(self.config.retry_delay_seconds)
            finally:
                self.logger.log("Shutting down automation.")
                context.close()

    def _play_round(self, page) -> None:
        self._ensure_connected(page)
        self._scroll_to_bottom(page)

        # Send /gamble command to start the game
        self._send_gamble_command(page)

        # Select the Coin Flip game mode (required for new UI)
        self._click_button(page, ["Coin Flip", "coinflip"], required=True)

        # Enter bet amount and submit
        self._fill_bet(page, self.config.bet_amount)
        self._click_button(page, ["Submit", "Bet", "Confirm"], required=True)

        # Choose Heads
        self._click_button(page, ["Heads"], required=True)

        # Wait for result
        result, total_coins = self._wait_for_round_result(page)
        self.round_count += 1
        total_label = total_coins if total_coins is not None else "unknown"
        self.logger.log(
            f"Round #{self.round_count}: bet={self.config.bet_amount} result={result.upper()} total_coins={total_label}"
        )

        # Handle win/loss scenarios
        if result == "win":
            clicked = self._click_button(page, ["Take coins", "Take Coins", "Claim"], required=False)
            if clicked:
                self.logger.log("Take coins clicked after win.")
            else:
                self.logger.log("Win detected but Take coins button was not found.")
        elif result == "loss":
            clicked = self._click_button(page, ["Retry"], required=False)
            if clicked:
                self.logger.log("Retry clicked after loss.")
            else:
                self.logger.log("Loss detected but Retry button was not found.")

    def _ensure_connected(self, page) -> None:
        reconnecting = page.get_by_text(re.compile(r"reconnecting", re.I)).first
        try:
            if reconnecting.is_visible(timeout=500):
                self.logger.log("Discord reconnecting detected. Waiting before retry...")
                self._interruptible_sleep(self.config.retry_delay_seconds)
                page.reload(wait_until="domcontentloaded", timeout=30_000)
        except Exception:  # noqa: BLE001
            return

    @staticmethod
    def _scroll_to_bottom(page) -> None:
        page.evaluate(
            """
            (() => {
                const scroller = document.querySelector('[class*="scroller"][data-list-id="chat-messages"]')
                    || document.querySelector('[class*="scrollerInner"]')?.parentElement;
                if (scroller) scroller.scrollTop = scroller.scrollHeight;
            })()
            """
        )

    def _send_gamble_command(self, page) -> None:
        """Send /gamble command to Discord chat to start the game."""
        try:
            # Find the message input box
            input_selector = 'div[role="textbox"][data-slate-editor="true"]'
            message_input = page.locator(input_selector).first
            message_input.wait_for(state="visible", timeout=self.config.action_timeout_ms)

            # Click to focus the input
            message_input.click()
            time.sleep(0.1)

            # Type the /gamble command
            page.keyboard.type("/gamble")
            time.sleep(0.3)  # Wait for autocomplete to appear

            # Press Enter to send the command
            page.keyboard.press("Enter")
            time.sleep(0.5)  # Wait for bot response

            self.logger.log("Sent /gamble command")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(
                f"Unable to send /gamble command: {type(exc).__name__}: {exc}"
            )

    def _fill_bet(self, page, amount: int) -> None:
        """Fill bet amount into the input field using multiple fallback strategies."""
        amount_text = str(amount)
        candidate_inputs = [
            ("label match (bet/amount/wager)", page.get_by_label(re.compile(r"bet|amount|wager", re.I)).first),
            ("placeholder match (bet/amount/wager)", page.get_by_placeholder(re.compile(r"bet|amount|wager", re.I)).first),
            ("selector match (input[bet/amount/number])", page.locator(
                'input[aria-label*="bet" i], input[placeholder*="bet" i], input[type="number"]'
            ).first),
            ("role spinbutton", page.get_by_role("spinbutton").first),
            ("textbox role (bet/amount/wager)", page.get_by_role("textbox", name=re.compile(r"bet|amount|wager", re.I)).first),
        ]

        for selector_desc, control in candidate_inputs:
            try:
                control.wait_for(state="visible", timeout=self.config.action_timeout_ms)
                control.click()
                control.fill(amount_text)
                self.logger.log(f"Bet amount {amount} filled using {selector_desc}")
                return
            except Exception:  # noqa: BLE001
                continue

        raise RuntimeError(
            f"Unable to locate bet input field after trying {len(candidate_inputs)} different selectors. "
            "Ensure the Discord bot interface is loaded and the page selectors are current. "
            "The /gamble command may not have been processed, or the game selection UI may not be showing the bet input."
        )

    def _click_button(self, page, labels: list[str], required: bool) -> bool:
        for label in labels:
            role_loc = page.get_by_role("button", name=re.compile(rf"^{re.escape(label)}$", re.I)).first
            fallback_loc = page.locator(f'button:has-text("{label}")').first
            for button in (role_loc, fallback_loc):
                try:
                    button.wait_for(state="visible", timeout=self.config.action_timeout_ms)
                    button.click()
                    return True
                except Exception:  # noqa: BLE001
                    continue

        if required:
            raise RuntimeError(
                f"Unable to click required button with any of these labels: {labels}. Ensure the Discord bot interface is loaded."
            )
        return False

    def _wait_for_round_result(self, page) -> tuple[str, int | None]:
        deadline = time.time() + self.config.round_timeout_seconds
        while time.time() < deadline and not self._stop.is_set():
            status_text = self._latest_message_text(page)
            retry_visible = self._is_retry_visible(page)
            result = self._determine_round_result(status_text, retry_visible)
            total_coins = self._extract_total_coins(status_text)
            if result is not None:
                return result, total_coins
            self._interruptible_sleep(0.3)

        raise RuntimeError(
            f"Timed out after {self.config.round_timeout_seconds}s waiting for coin flip result."
        )

    def _is_retry_visible(self, page) -> bool:
        retry_button = page.get_by_role("button", name=re.compile(r"^retry$", re.I)).first
        try:
            return retry_button.is_visible(timeout=300)
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _latest_message_text(page) -> str:
        messages = page.locator('[data-list-item-id^="chat-messages"]')
        try:
            count = messages.count()
        except Exception:  # noqa: BLE001
            return ""

        if count <= 0:
            return ""

        text_parts: list[str] = []
        start = max(0, count - 3)
        for i in range(start, count):
            try:
                text_parts.append(messages.nth(i).inner_text(timeout=500))
            except Exception:  # noqa: BLE001
                continue
        return "\n".join(text_parts)

    @staticmethod
    def _determine_round_result(status_text: str, retry_visible: bool) -> str | None:
        lowered = status_text.lower()
        if re.search(r"\b(you\s+lose|you\s+lost|lost\b|loss\b)\b", lowered):
            return "loss"
        if re.search(r"\b(you\s+win|you\s+won|won\b|win\b)\b", lowered):
            return "win"
        if retry_visible:
            return "loss"
        return None

    @staticmethod
    def _extract_total_coins(status_text: str) -> int | None:
        match = re.search(r"your\s+coins\s*:\s*([0-9,]+)", status_text, flags=re.I)
        if not match:
            return None
        return int(match.group(1).replace(",", ""))

    def _interruptible_sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end and not self._stop.is_set():
            time.sleep(0.1)


class CoinFlipHeadsUI:
    def __init__(self, config_path: Path) -> None:
        if tk is None:  # pragma: no cover - environment specific
            raise RuntimeError("tkinter is not available. Run with --no-ui or install python3-tk.")

        self.config_path = config_path
        self.config = load_config(config_path)
        self.root = tk.Tk()
        self.root.title("Coin Flip Gamble (Heads)")
        self.root.geometry("760x420")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._thread: threading.Thread | None = None
        self._bot: CoinFlipHeadsBot | None = None
        self._logger = AppLogger(self._append_log)

        self._build_ui()

    def _build_ui(self) -> None:
        frm = tk.Frame(self.root, padx=10, pady=10)
        frm.pack(fill="x")

        tk.Label(frm, text="Discord Channel URL").grid(row=0, column=0, sticky="w")
        self.url_var = tk.StringVar(value=self.config.channel_url)
        tk.Entry(frm, textvariable=self.url_var, width=80).grid(row=0, column=1, columnspan=5, sticky="we", pady=2)

        tk.Label(frm, text="Bet Amount").grid(row=1, column=0, sticky="w")
        self.bet_var = tk.StringVar(value=str(self.config.bet_amount))
        tk.Entry(frm, textvariable=self.bet_var, width=14).grid(row=1, column=1, sticky="w", pady=2)

        self.headless_var = tk.BooleanVar(value=self.config.headless)
        tk.Checkbutton(frm, text="Headless", variable=self.headless_var).grid(row=1, column=2, sticky="w")

        self.start_btn = tk.Button(frm, text="Start", width=12, command=self.start)
        self.start_btn.grid(row=1, column=3, padx=4)
        self.stop_btn = tk.Button(frm, text="Stop", width=12, command=self.stop, state="disabled")
        self.stop_btn.grid(row=1, column=4, padx=4)
        tk.Button(frm, text="Save Config", width=12, command=self._save_fields_to_config).grid(row=1, column=5, padx=4)

        self.log_widget = tk.Text(self.root, height=18, state="disabled")
        self.log_widget.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        self._append_log("Loaded configuration. Edit bet and press Start.")

    def _append_log(self, line: str) -> None:
        def _write() -> None:
            self.log_widget.configure(state="normal")
            self.log_widget.insert("end", line + "\n")
            self.log_widget.see("end")
            self.log_widget.configure(state="disabled")

        try:
            self.root.after(0, _write)
        except Exception:  # noqa: BLE001
            return

    def _save_fields_to_config(self) -> None:
        try:
            self._sync_config_from_fields()
            save_config(self.config, self.config_path)
            self._append_log(f"Saved config to {self.config_path}")
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"Invalid settings: {exc}")

    def _sync_config_from_fields(self) -> None:
        channel_url = self.url_var.get().strip()
        if not channel_url:
            raise ValueError("channel url must not be empty")
        bet_amount = int(self.bet_var.get().strip())
        if bet_amount <= 0:
            raise ValueError("bet amount must be > 0")

        self.config = GambleConfig(
            channel_url=channel_url,
            bet_amount=bet_amount,
            headless=bool(self.headless_var.get()),
            session_dir=self.config.session_dir,
            action_timeout_ms=self.config.action_timeout_ms,
            round_timeout_seconds=self.config.round_timeout_seconds,
            retry_delay_seconds=self.config.retry_delay_seconds,
        )

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            self._append_log("Automation is already running.")
            return

        try:
            self._sync_config_from_fields()
            save_config(self.config, self.config_path)
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"Invalid settings: {exc}")
            return

        self._bot = CoinFlipHeadsBot(self.config, self._logger)
        self._thread = threading.Thread(target=self._bot.run, daemon=True)
        self._thread.start()

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._append_log("Automation started.")

    def stop(self) -> None:
        if self._bot is not None:
            self._bot.request_stop()
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")
        self._append_log("Stop requested.")

    def _on_close(self) -> None:
        self.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def _run_no_ui(config_path: Path) -> None:
    config = load_config(config_path)
    logger = AppLogger()
    bot = CoinFlipHeadsBot(config, logger)

    def _sigint_handler(_sig, _frame) -> None:  # pragma: no cover - signal handling
        logger.log("SIGINT received. Stopping...")
        bot.request_stop()

    signal.signal(signal.SIGINT, _sigint_handler)
    bot.run()


def main() -> None:
    parser = argparse.ArgumentParser(description="Standalone Coin Flip Heads automation app")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="Path to JSON config")
    parser.add_argument(
        "--no-ui",
        action="store_true",
        help="Run directly from config without the Tk UI (Ctrl+C to stop)",
    )
    args = parser.parse_args()

    config_path = Path(args.config).resolve()

    if args.no_ui:
        _run_no_ui(config_path)
        return

    app = CoinFlipHeadsUI(config_path)

    def _sigint_ui(_sig, _frame) -> None:  # pragma: no cover - signal handling
        app.stop()

    signal.signal(signal.SIGINT, _sigint_ui)
    app.run()


if __name__ == "__main__":
    main()
