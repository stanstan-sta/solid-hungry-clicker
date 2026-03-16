#!/usr/bin/env python3
"""
Hungry-RNG Auto-Clicker for Discord
====================================
Automatically clicks the blue "Roll!" button in a Discord channel
for the Hungry-RNG APP bot. Runs in its own browser window so your
normal computer use is completely unaffected.

Usage:
    pip install playwright pynput
    playwright install chromium
    python hungry_clicker.py

Press F8 to start / stop the auto-clicker at any time.
"""

import random
import time
import tkinter as tk
from datetime import datetime

from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout
from pynput import keyboard

# ──────────────────────────────────────────────────────────────────
# ██  SETTINGS – edit these to match your setup
# ──────────────────────────────────────────────────────────────────

# Discord channel URL for the Hungry-RNG bot chat.
# Right-click the channel in Discord → Copy Link, then paste here.
CHANNEL_URL: str = "https://discord.com/channels/YOUR_SERVER_ID/YOUR_CHANNEL_ID"

# Cooldown in seconds between roll attempts (minimum wait after each click).
COOLDOWN: float = 2.5

# Random extra delay range (seconds) added after every click for human-like timing.
RANDOM_DELAY_MIN: float = 0.4
RANDOM_DELAY_MAX: float = 0.9

# Run the browser with a visible window (False = hidden / headless).
HEADLESS: bool = False

# Directory where login cookies / session data are persisted.
# Delete this folder to force a fresh login.
SESSION_DIR: str = "discord_session"

# Hotkey to toggle the auto-clicker on/off.
TOGGLE_KEY = keyboard.Key.f8

# How long (seconds) to wait for the Roll! button before retrying.
BUTTON_TIMEOUT: int = 5

# How many extra scroll-to-bottom attempts when the button is not found.
# Each attempt scrolls, waits briefly, then checks again.
AUTOSCROLL_RETRIES: int = 3

# How long (seconds) to wait before attempting reconnection.
RECONNECT_WAIT: int = 10

# ──────────────────────────────────────────────────────────────────


class JsonLogger:
    """Simple threadsafe console logger with timestamps."""

    @staticmethod
    def log(message: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        print(f"[{ts}] {message}", flush=True)


class StatusWindow:
    """Tiny always-on-top Tkinter window showing live stats."""

    def __init__(self) -> None:
        self.root = tk.Tk()
        self.root.title("Hungry-RNG Clicker")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        self.root.geometry("310x80")
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self._label = tk.Label(
            self.root,
            text="Status: Paused  |  Rolls: 0",
            font=("Consolas", 11),
            padx=10,
            pady=10,
        )
        self._label.pack(expand=True, fill="both")
        self._should_close = False

    # ── public helpers ──────────────────────────────────────────

    def update(self, active: bool, count: int) -> None:
        """Schedule a label update on the Tk main thread."""
        status = "Active" if active else "Paused"
        text = f"Rolling… #{count}  |  Status: {status}"
        try:
            self.root.after(0, lambda: self._label.config(text=text))
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


class HungryClicker:
    """Core automation: opens Discord in a persistent Chromium profile,
    scrolls the chat to the bottom, and clicks the Roll! button in a loop.
    """

    def __init__(self, status_window: StatusWindow) -> None:
        self.active = False          # controlled by F8
        self.roll_count = 0
        self.running = True          # master kill-switch
        self._status = status_window
        self._log = JsonLogger.log

    # ── hotkey listener (runs in its own daemon thread) ─────────

    def start_hotkey_listener(self) -> None:
        """Listen for the global toggle hotkey in a background thread."""

        def _on_press(key: keyboard.Key) -> None:
            if key == TOGGLE_KEY:
                self.active = not self.active
                state = "ACTIVE" if self.active else "PAUSED"
                self._log(f"F8 pressed → {state}")

        listener = keyboard.Listener(on_press=_on_press)
        listener.daemon = True
        listener.start()
        self._log(f"Hotkey listener ready – press {TOGGLE_KEY.name.upper()} to toggle")

    # ── main loop ───────────────────────────────────────────────

    def run(self) -> None:
        """Launch the browser and enter the click loop."""
        self._log("Launching Chromium with persistent session…")

        with sync_playwright() as pw:
            context = pw.chromium.launch_persistent_context(
                user_data_dir=SESSION_DIR,
                headless=HEADLESS,
                channel="chrome",               # use installed Chrome
                args=[
                    "--disable-blink-features=AutomationControlled",
                ],
                viewport={"width": 1280, "height": 900},
                ignore_default_args=["--enable-automation"],
            )

            page = context.pages[0] if context.pages else context.new_page()
            self._navigate(page)
            self._log("Browser ready. Waiting for F8 to start…")

            try:
                while self.running:
                    # Keep the Tkinter window responsive.
                    if not self._status.pump():
                        self._log("Status window closed – shutting down.")
                        break

                    self._status.update(self.active, self.roll_count)

                    if not self.active:
                        time.sleep(0.15)
                        continue

                    self._ensure_connected(page)
                    self._scroll_to_bottom(page)

                    # Try to click; if the button isn't visible, autoscroll
                    # several times before giving up for this cycle.
                    if not self._try_click_roll(page):
                        self._autoscroll_and_retry(page)

                    delay = COOLDOWN + random.uniform(RANDOM_DELAY_MIN, RANDOM_DELAY_MAX)
                    self._interruptible_sleep(delay)
            except KeyboardInterrupt:
                self._log("Interrupted by user.")
            finally:
                self._log(f"Shutting down. Total rolls: {self.roll_count}")
                context.close()

    # ── navigation / reconnection ───────────────────────────────

    def _navigate(self, page) -> None:
        """Go to the configured Discord channel URL."""
        self._log(f"Navigating to {CHANNEL_URL}")
        try:
            page.goto(CHANNEL_URL, wait_until="domcontentloaded", timeout=30_000)
        except PwTimeout:
            self._log("Page load timed out – will retry on next loop.")

    def _ensure_connected(self, page) -> None:
        """Detect Discord disconnection and attempt to recover."""
        # Discord shows various reconnection banners when disconnected.
        disconnected = page.locator("text=Reconnecting").first
        try:
            if disconnected.is_visible(timeout=500):
                self._log("Discord disconnected – waiting to reconnect…")
                time.sleep(RECONNECT_WAIT)
                page.reload(wait_until="domcontentloaded", timeout=30_000)
                self._log("Reconnected.")
        except (PwTimeout, Exception):
            pass  # not disconnected – carry on

    # ── scrolling ───────────────────────────────────────────────

    @staticmethod
    def _scroll_to_bottom(page) -> None:
        """Scroll the Discord message list to the very bottom so the newest
        Roll! button is in view.
        """
        page.evaluate(
            """
            (() => {
                const scroller = document.querySelector('[class*="scroller"][data-list-id="chat-messages"]')
                    || document.querySelector('[class*="scrollerInner"]')?.parentElement;
                if (scroller) scroller.scrollTop = scroller.scrollHeight;
            })()
            """
        )

    # ── clicking ────────────────────────────────────────────────

    def _try_click_roll(self, page) -> bool:
        """Locate and click the Roll! button, using multiple strategies.

        Returns True if a click was performed, False otherwise.
        """
        # Strategy 1: text-based locator (most reliable for labelled buttons).
        btn = page.locator('button:has-text("Roll!")').last

        try:
            btn.wait_for(state="visible", timeout=BUTTON_TIMEOUT * 1000)
            btn.click()
            self.roll_count += 1
            self._log(f"Roll #{self.roll_count} clicked ✓")
            return True
        except (PwTimeout, Exception):
            pass

        # Strategy 2: look for a blue-styled button that contains "Roll!"
        fallback = page.locator(
            'button[style*="background-color"]:has-text("Roll!")'
        ).last
        try:
            fallback.wait_for(state="visible", timeout=BUTTON_TIMEOUT * 1000)
            fallback.click()
            self.roll_count += 1
            self._log(f"Roll #{self.roll_count} clicked (fallback) ✓")
            return True
        except (PwTimeout, Exception):
            return False

    def _autoscroll_and_retry(self, page) -> None:
        """Aggressively scroll to the bottom multiple times and re-check for
        the Roll! button after each scroll.  Useful when Discord lazy-loads
        messages or the chat hasn't caught up yet.
        """
        for attempt in range(1, AUTOSCROLL_RETRIES + 1):
            self._log(
                f"Button not visible – autoscroll attempt {attempt}/{AUTOSCROLL_RETRIES}"
            )
            # Press End key as an alternative scroll method.
            page.keyboard.press("End")
            time.sleep(0.3)
            self._scroll_to_bottom(page)
            time.sleep(0.5)

            if self._try_click_roll(page):
                return  # success after scrolling

        self._log("Roll! button not found after autoscroll – will retry next cycle.")

    # ── helpers ─────────────────────────────────────────────────

    def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep in small increments so the Tkinter window stays responsive
        and the hotkey can pause immediately.
        """
        end = time.time() + seconds
        while time.time() < end and self.running:
            if not self._status.pump():
                self.running = False
                break
            self._status.update(self.active, self.roll_count)
            time.sleep(0.05)


# ──────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────

def main() -> None:
    """Set up the status window, hotkey listener, and start the clicker."""
    status = StatusWindow()
    clicker = HungryClicker(status)
    clicker.start_hotkey_listener()
    clicker.run()


if __name__ == "__main__":
    main()
