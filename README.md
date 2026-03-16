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