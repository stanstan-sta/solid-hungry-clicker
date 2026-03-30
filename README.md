# solid-hungry-clicker

Auto-clicks the blue **"Roll!"** button for the **Hungry-RNG APP** bot in Discord — runs in its own browser window so your normal computer use is completely unaffected.

| Feature | Details |
|---|---|
| Browser engine | Microsoft Playwright (Chromium) |
| Persistent login | Cookies saved in `discord_session/` — log in once |
| Global hotkey | **F8** to start / stop |
| Status window | Tiny always-on-top Tkinter panel showing roll count |
| Smart retry | If the button isn't found, waits and retries automatically |
| Auto-reconnect | Detects Discord disconnection and reloads |

---

## Setup in 90 Seconds

### 1. Install dependencies

```bash
pip install playwright pynput
playwright install chromium
```

> **Note:** `tkinter` ships with Python on Windows and macOS. On Linux install it with
> `sudo apt-get install python3-tk` (Debian/Ubuntu) or the equivalent for your distro.

### 2. Get your Discord channel URL

1. Open Discord (desktop app or browser).
2. Navigate to the channel where the **Hungry-RNG** bot posts.
3. Right-click the channel name → **Copy Link**.
4. Paste the URL into the `CHANNEL_URL` variable at the top of `hungry_clicker.py`.

### 3. First run — log in once

```bash
python hungry_clicker.py
```

A Chromium window will open. **Log in to Discord manually** — your session is saved in the `discord_session/` folder so you won't need to log in again.

### 4. Start rolling

Press **F8** to toggle the auto-clicker on/off. The status window shows:

```
Rolling… #42  |  Status: Active
```

Every successful click is also logged to the console.

### Multi-`/roll` burst mode

Press **F9** to queue and send multiple `/roll` commands in rapid succession (milliseconds apart).

Configure the burst commands in `ROLL_COMMANDS_TEXT` using new lines or commas:

```python
ROLL_COMMANDS_TEXT = "/roll\n/roll 1d6\n/roll 2d20"
# or: "/roll,/roll 1d6,/roll 2d20"
```

Timing controls:

```python
BATCH_INITIAL_DELAY_MS = 120  # wait before first /roll
BATCH_COMMAND_DELAY_MS = 40   # wait between /roll commands
```

---

## Standalone Coin Flip Gamble App (Heads Strategy)

This repository now includes a separate standalone app at:

`coin_flip_heads/app.py`

It automates the Discord casino Coin Flip workflow with a fixed **Heads** strategy:

1. Fill bet amount
2. Submit bet
3. Click **Heads**
4. Read win/loss result
5. Click **Retry** on loss
6. Repeat until stopped

### Configure bet amount

- UI mode: enter the bet in the **Bet Amount** field before pressing **Start**
- Config mode: edit `coin_flip_heads/config.json` (`bet_amount`)

The config can be changed any time before starting a new session.

### Run

```bash
python coin_flip_heads/app.py
```

### Headless / no-UI mode

```bash
python coin_flip_heads/app.py --no-ui
```

Use `Ctrl+C` in no-UI mode, or the **Stop** button in UI mode, to safely stop the loop.

### Setup notes

- Uses **Python Playwright** (`playwright.sync_api`)
- Reuses a persistent browser session in `discord_session_gamble/`
- Logs each round with bet, win/loss, and parsed `Your coins` total (when present)
- Handles reconnect/timeouts by retrying the next loop

### UI references

- Main Gamble screen: https://github.com/user-attachments/assets/998102d8-bb97-4cfe-85ff-53def0b97eeb
- Bet entry screen: https://github.com/user-attachments/assets/d5967e2d-edd8-4dee-a856-cf866135e060
- Heads/Tails choice: https://github.com/user-attachments/assets/b0717e36-44a5-4c49-9f72-3d4631a4211f
- Retry screen: https://github.com/user-attachments/assets/e82750d8-1575-4438-a890-32ca184d3e1d

---

## Running Minimised / in Background

- **Windows/macOS:** Simply minimize the Chromium window. The clicker keeps running. The tiny status window stays on top so you can see progress.
- **Headless mode:** Set `HEADLESS = True` in the settings block. The browser runs invisibly in the background.

---

## Configuration

All settings are at the top of `hungry_clicker.py`:

```python
CHANNEL_URL   = "https://discord.com/channels/..."  # your channel link
COOLDOWN      = 2.5       # seconds between clicks
HEADLESS      = False     # True = invisible browser
SESSION_DIR   = "discord_session"
TOGGLE_KEY    = Key.f8    # global hotkey
BATCH_TRIGGER_KEY = Key.f9
```

---

## Docker (optional — full container isolation)

```bash
docker run --rm -it \
  -v $(pwd)/discord_session:/app/discord_session \
  mcr.microsoft.com/playwright/python:v1.40.0-jammy \
  bash -c "pip install pynput && cd /app && python hungry_clicker.py"
```

> In Docker the browser runs headless by default. Set `HEADLESS = True` in the script.

---

## License

MIT
