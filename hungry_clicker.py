#!/usr/bin/env python3
"""
Hungry-RNG Auto-Clicker for Discord (Multithreaded)
====================================================
Automatically clicks the blue "Roll!" button in multiple Discord channels.
Runs in its own browser window for each channel specified.

Usage:
    pip install playwright pynput
    playwright install chromium
    python hungry_clicker.py

Press F8 to start / stop all channels.
Press F9 to trigger a /roll burst across all channels.
"""

import random
import re
import threading
import time
import tkinter as tk
from datetime import datetime
from queue import Empty, SimpleQueue

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
from pynput import keyboard

# ──────────────────────────────────────────────────────────────────
# ██  SETTINGS – edit these to match your setup
# ──────────────────────────────────────────────────────────────────

# List of Discord channel URLs. Each will open in its own thread/browser.
# Right-click a channel in Discord → Copy Link, then paste here.
CHANNEL_URLS: list[str] = [
    "https://discord.com/channels/YOUR_SERVER_ID/YOUR_CHANNEL_ID",
]

# Cooldown in seconds between roll attempts (minimum wait after each click).
COOLDOWN: float = 2.5

# Random extra delay range (seconds) added after every click for human-like timing.
RANDOM_DELAY_MIN: float = 0.4
RANDOM_DELAY_MAX: float = 0.9

# Run the browser with a visible window (False = hidden / headless).
HEADLESS: bool = False

# Base directory where login cookies / session data are persisted.
# Each thread uses a sub-folder: discord_session_0, discord_session_1, …
SESSION_DIR_BASE: str = "discord_session"

# Hotkey to toggle all channels on/off.
TOGGLE_KEY = keyboard.Key.f8

# Hotkey to trigger a burst of multiple /roll commands on all channels.
BATCH_TRIGGER_KEY = keyboard.Key.f9

# Commands to send when the burst hotkey is pressed. Supports newline or comma-separated values.
ROLL_COMMANDS_TEXT: str = "/roll"

# Delay before firing the first command in a burst (milliseconds).
BATCH_INITIAL_DELAY_MS: int = 120

# Delay between commands in a burst (milliseconds).
BATCH_COMMAND_DELAY_MS: int = 40

# How long (seconds) to wait for the Roll! button before retrying.
BUTTON_TIMEOUT: int = 5

# How long (ms) to wait for Discord's slash-command autocomplete menu to appear.
AUTOCOMPLETE_TIMEOUT_MS: int = 2_000

# Delay (seconds) between clicking successive Roll! buttons in a single cycle.
MULTI_CLICK_DELAY: float = 0.3

# How many scroll-and-retry attempts to make when the Roll! button is not visible.
AUTOSCROLL_RETRIES: int = 3

# How long (seconds) to wait before attempting reconnection.
RECONNECT_WAIT: int = 10

# Button labels to auto-click for Hungry-RNG game flows.
GAME_BUTTON_TEXT_PATTERN = re.compile(
    r"Roll!|Play Again|Take coins|Heads\s*-\s*go again|Tails\s*-\s*go again",
    re.I,
)

# ──────────────────────────────────────────────────────────────────


class ConsoleLogger:
    """Simple threadsafe console logger with timestamps."""

    @staticmethod
    def log(message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {message}", flush=True)


class StatusWindow:
    """Always-on-top Tkinter window showing aggregate and per-channel stats."""

    def __init__(self, channel_count: int) -> None:
        self.root = tk.Tk()
        self.root.title("Hungry-RNG Clicker (Multi)")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self.root.geometry(f"350x{100 + (channel_count * 20)}")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._main_label = tk.Label(
            self.root,
            text="Status: Paused  |  Total: 0",
            font=("Consolas", 12, "bold"),
            pady=10,
        )
        self._main_label.pack()

        self._stats_labels: list[tk.Label] = []
        for i in range(channel_count):
            lbl = tk.Label(
                self.root,
                text=f"Channel {i + 1}: 0 rolls",
                font=("Consolas", 10),
            )
            lbl.pack()
            self._stats_labels.append(lbl)

        self._should_close = False

    # ── public helpers ──────────────────────────────────────────

    def update(self, active: bool, roll_counts: list[int]) -> None:
        """Schedule a label update on the Tk main thread."""
        status = "ACTIVE" if active else "PAUSED"
        total = sum(roll_counts)
        try:
            self.root.after(
                0,
                lambda: self._main_label.config(
                    text=f"Status: {status}  |  Total: {total}",
                    fg="green" if active else "red",
                ),
            )
            for i, count in enumerate(roll_counts):
                self.root.after(
                    0,
                    lambda i=i, count=count: self._stats_labels[i].config(
                        text=f"Channel {i + 1}: {count} rolls"
                    ),
                )
        except tk.TclError:
            pass  # window already destroyed

    def pump(self) -> bool:
        """Drive the Tk event loop from the caller's thread.

        Returns False when the user closes the window.
        """
        try:
            self.root.update_idletasks()
            self.root.update()
        except tk.TclError:
            return False
        return not self._should_close

    # ── private ─────────────────────────────────────────────────

    def _on_close(self) -> None:
        self._should_close = True
        self.root.destroy()


class ClickerThread(threading.Thread):
    """Worker thread: manages one Playwright browser instance for a single URL."""

    def __init__(self, index: int, url: str, master: "HungryClickerMaster") -> None:
        super().__init__(daemon=True)
        self.index = index
        self.url = url
        self.master = master
        self.roll_count = 0
        self.batch_count = 0
        self.session_dir = f"{SESSION_DIR_BASE}_{index}"
        self._log = lambda msg: ConsoleLogger.log(f"[Ch{index + 1}] {msg}")

    # ── thread entry ────────────────────────────────────────────

    def run(self) -> None:
        self._log(f"Starting automation for {self.url}")
        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=self.session_dir,
                headless=HEADLESS,
                args=["--disable-blink-features=AutomationControlled"],
                viewport={"width": 1280, "height": 900},
                ignore_default_args=["--enable-automation"],
            )

            page = context.pages[0] if context.pages else context.new_page()
            self._navigate(page)
            self._log("Browser ready. Waiting for F8 to start…")

            try:
                while self.master.running:
                    try:
                        self.master.pending_bursts[self.index].get_nowait()
                        self._send_roll_burst(page)
                    except Empty:
                        pass

                    if not self.master.active:
                        time.sleep(0.5)
                        continue

                    self._ensure_connected(page)
                    self._scroll_to_bottom(page)

                    if not self._try_click_roll(page):
                        self._autoscroll_and_retry(page)

                    delay = COOLDOWN + random.uniform(RANDOM_DELAY_MIN, RANDOM_DELAY_MAX)
                    self._interruptible_sleep(delay)
            except Exception as exc:
                self._log(f"Unexpected error: {exc}")
            finally:
                self._log(f"Shutting down. Rolls: {self.roll_count}")
                context.close()

    # ── navigation / reconnection ───────────────────────────────

    def _navigate(self, page) -> None:
        self._log(f"Navigating to {self.url}")
        try:
            page.goto(self.url, wait_until="domcontentloaded", timeout=30_000)
        except PwTimeout:
            self._log("Page load timed out – will retry on next loop.")

    def _ensure_connected(self, page) -> None:
        disconnected = page.locator("text=Reconnecting").first
        try:
            if disconnected.is_visible(timeout=500):
                self._log("Discord disconnected – waiting to reconnect…")
                time.sleep(RECONNECT_WAIT)
                page.reload(wait_until="domcontentloaded", timeout=30_000)
                self._log("Reconnected.")
        except (PwTimeout, Exception):
            pass

    # ── scrolling ───────────────────────────────────────────────

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

    # ── burst / slash-command logic ─────────────────────────────

    @staticmethod
    def _burst_commands() -> list[str]:
        """Return configured slash commands from the text setting."""
        raw = ROLL_COMMANDS_TEXT.replace(",", "\n")
        commands = []
        for item in (part.strip() for part in raw.splitlines()):
            if not item:
                continue
            if item.startswith("/"):
                commands.append(item)
        return commands

    def _send_roll_burst(self, page) -> None:
        """Send all configured /roll commands with millisecond spacing."""
        commands = self._burst_commands()
        if not commands:
            self._log("No valid /roll commands configured in ROLL_COMMANDS_TEXT.")
            return

        self._ensure_connected(page)
        self._scroll_to_bottom(page)

        self._log(f"Running /roll burst ({len(commands)} commands)…")
        time.sleep(max(BATCH_INITIAL_DELAY_MS, 0) / 1000)

        for idx, command in enumerate(commands, start=1):
            if not self.master.running:
                return
            if not self._submit_roll_command(page, command):
                self._log(f"Failed to send burst command {idx}/{len(commands)}: {command}")
                continue
            self._log(f"Burst command {idx}/{len(commands)} sent ✓ {command}")
            if idx < len(commands):
                time.sleep(max(BATCH_COMMAND_DELAY_MS, 0) / 1000)

        self.batch_count += 1
        self._log(f"/roll burst complete #{self.batch_count}")

    def _submit_roll_command(self, page, command: str) -> bool:
        """Focus Discord composer, click the autocomplete item, and submit.

        Primary path: wait for the autocomplete popup, then click the matching
        command item directly so Discord registers it as an app command (not
        plain text).  Falls back to Tab→Enter if the direct click fails.
        """
        selectors = (
            'div[role="textbox"][data-slate-editor="true"][aria-label^="Message"]',
            'div[role="textbox"][data-slate-editor="true"]',
        )
        for selector in selectors:
            composer = page.locator(selector).first
            try:
                composer.wait_for(state="visible", timeout=3_000)
                composer.click()
                break
            except (PwTimeout, Exception):
                continue
        else:
            return False

        parts = command.strip().split()
        slash_command = parts[0] if parts else command.strip()
        command_args = parts[1:]

        page.keyboard.press("ControlOrMeta+A")
        page.keyboard.press("Backspace")
        page.keyboard.type(slash_command)

        # Primary: locate the specific command button in the autocomplete popup
        # and click it so Discord treats it as a slash-command invocation.
        autocomplete_item = (
            page.locator(
                '[class*="autocomplete"] [role="button"],'
                ' [data-list-id="autocomplete-results"] [role="option"]'
            )
            .filter(has_text=slash_command)
            .first
        )
        command_selected = False
        try:
            autocomplete_item.wait_for(state="visible", timeout=AUTOCOMPLETE_TIMEOUT_MS)
            autocomplete_item.click()
            command_selected = True
        except (PwTimeout, Exception):
            self._log("Autocomplete item not found – trying Tab+Enter fallback.")

        # Fallback: press Tab to select the first autocomplete suggestion, then Enter.
        autocomplete = page.locator(
            '[class*="autocomplete"], [data-list-id="autocomplete-results"], ul[role="listbox"]'
        ).first
        try:
            if not command_selected:
                autocomplete.wait_for(state="visible", timeout=AUTOCOMPLETE_TIMEOUT_MS)
                page.keyboard.press("Tab")
                command_selected = True
        except (PwTimeout, Exception):
            pass  # no autocomplete appeared – fall through to plain Enter

        if slash_command == "/gamble":
            mode = command_args[0] if command_args else "coinflip"
            self._select_gamble_mode(page, mode)
        elif command_args:
            page.keyboard.type(f" {' '.join(command_args)}")

        time.sleep(0.1)  # let Discord register command/options before submitting
        page.keyboard.press("Enter")
        return True

    def _select_gamble_mode(self, page, mode: str) -> None:
        normalized_mode = mode.strip() or "coinflip"
        page.keyboard.type(f" {normalized_mode}")

        option_item = (
            page.locator(
                '[class*="autocomplete"] [role="button"],'
                ' [data-list-id="autocomplete-results"] [role="option"],'
                ' [role="listbox"] [role="option"]'
            )
            .filter(has_text=normalized_mode)
            .first
        )
        try:
            option_item.wait_for(state="visible", timeout=AUTOCOMPLETE_TIMEOUT_MS)
            option_item.click()
        except (PwTimeout, Exception):
            try:
                autocomplete = page.locator(
                    '[class*="autocomplete"], [data-list-id="autocomplete-results"], ul[role="listbox"]'
                ).first
                autocomplete.wait_for(state="visible", timeout=AUTOCOMPLETE_TIMEOUT_MS)
                page.keyboard.press("Tab")
            except (PwTimeout, Exception):
                pass

    # ── clicking ────────────────────────────────────────────────

    def _try_click_roll(self, page) -> bool:
        """Locate and click every visible Roll! button.

        Returns True if at least one click was performed, False otherwise.
        """
        # Strategy 1: text-based locator (most reliable for labelled buttons).
        buttons = page.locator("button").filter(has_text=GAME_BUTTON_TEXT_PATTERN)

        try:
            buttons.first.wait_for(state="visible", timeout=BUTTON_TIMEOUT * 1000)
        except (PwTimeout, Exception):
            pass
        else:
            clicked = False
            count = buttons.count()
            for i in range(count):
                btn = buttons.nth(i)
                try:
                    if btn.is_visible():
                        btn.click()
                        self.roll_count += 1
                        self._log(f"Roll #{self.roll_count} clicked ✓")
                        clicked = True
                        if i < count - 1:
                            time.sleep(MULTI_CLICK_DELAY)
                except (PwTimeout, Exception):
                    pass
            if clicked:
                return True

        # Strategy 2: blue-styled fallback containing known game action labels.
        fallback_buttons = page.locator('button[style*="background-color"]').filter(
            has_text=GAME_BUTTON_TEXT_PATTERN
        )
        try:
            fallback_buttons.first.wait_for(state="visible", timeout=BUTTON_TIMEOUT * 1000)
        except (PwTimeout, Exception):
            return False

        clicked = False
        count = fallback_buttons.count()
        for i in range(count):
            btn = fallback_buttons.nth(i)
            try:
                if btn.is_visible():
                    btn.click()
                    self.roll_count += 1
                    self._log(f"Roll #{self.roll_count} clicked (fallback) ✓")
                    clicked = True
                    if i < count - 1:
                        time.sleep(MULTI_CLICK_DELAY)
            except (PwTimeout, Exception):
                pass
        return clicked

    def _autoscroll_and_retry(self, page) -> None:
        for attempt in range(1, AUTOSCROLL_RETRIES + 1):
            self._log(
                f"Button not visible – autoscroll attempt {attempt}/{AUTOSCROLL_RETRIES}"
            )
            page.keyboard.press("End")
            time.sleep(0.3)
            self._scroll_to_bottom(page)
            time.sleep(0.5)
            if self._try_click_roll(page):
                return

        self._log("Game action button not found after autoscroll – will retry next cycle.")

    # ── helpers ─────────────────────────────────────────────────

    def _interruptible_sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end and self.master.running:
            time.sleep(0.1)


class HungryClickerMaster:
    """Orchestrates multiple ClickerThreads and the global UI/hotkeys."""

    def __init__(self) -> None:
        self.active = False
        self.running = True
        self.urls = CHANNEL_URLS
        self.status = StatusWindow(len(self.urls))
        self.pending_bursts: list[SimpleQueue[int]] = [SimpleQueue() for _ in self.urls]
        self.threads = [
            ClickerThread(i, url, self) for i, url in enumerate(self.urls)
        ]

    def start_hotkey_listener(self) -> None:
        """Listen for F8 (toggle) and F9 (burst) in a background thread."""

        def _on_press(key: keyboard.Key) -> None:
            if key == TOGGLE_KEY:
                self.active = not self.active
                state = "ACTIVE" if self.active else "PAUSED"
                ConsoleLogger.log(f"F8 → GLOBAL {state}")
            elif key == BATCH_TRIGGER_KEY:
                for q in self.pending_bursts:
                    q.put(1)
                ConsoleLogger.log("F9 → GLOBAL BURST TRIGGERED")

        listener = keyboard.Listener(on_press=_on_press)
        listener.daemon = True
        listener.start()
        ConsoleLogger.log(
            f"Hotkeys ready – {TOGGLE_KEY.name.upper()} toggle, "
            f"{BATCH_TRIGGER_KEY.name.upper()} multi-/roll burst"
        )

    def run(self) -> None:
        ConsoleLogger.log(f"Starting {len(self.threads)} channel thread(s)…")
        for t in self.threads:
            t.start()

        try:
            while self.running:
                if not self.status.pump():
                    ConsoleLogger.log("Status window closed – shutting down.")
                    self.running = False
                    break

                counts = [t.roll_count for t in self.threads]
                self.status.update(self.active, counts)
                time.sleep(0.1)
        except KeyboardInterrupt:
            ConsoleLogger.log("Interrupted by user.")
            self.running = False
        finally:
            ConsoleLogger.log("Master shutting down.")


# ──────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────


def main() -> None:
    """Set up the master, hotkey listener, and start all channel threads."""
    master = HungryClickerMaster()
    master.start_hotkey_listener()
    master.run()


if __name__ == "__main__":
    main()
