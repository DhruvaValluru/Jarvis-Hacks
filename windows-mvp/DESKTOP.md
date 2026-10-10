# Alfred desktop app

The Alfred app is a normal window plus a tray icon. You type what you want, watch what Alfred is doing, answer its questions (Allow or Deny), and press **STOP** whenever you like. Telegram runs through the same app, so there is one conversation and one Stop for everything.

Until the installer exists (task K1), run it from source.

## Run it from source

```bat
cd windows-mvp
setup.bat
.venv\Scripts\python -m pip install pywebview pystray
.venv\Scripts\python -m jarvis.app
```

- `pywebview` gives Alfred its own window (it uses Microsoft Edge WebView2, which Windows 11 already has). Without it, Alfred opens in your default browser instead.
- `pystray` adds the tray icon. Without it there is no tray icon, so closing the window quits Alfred: it never keeps running where you can't see it.
- Neither is in `requirements.lock.txt` yet, so `test.bat` and CI don't need them.

Options:

| Command | What it does |
|---|---|
| `python -m jarvis.app` | The app. |
| `python -m jarvis.app --no-window` | Use your default browser instead of Alfred's window. The console prints a link that works once, in case the browser doesn't open. |
| `python -m jarvis.app --fake-core` | A scripted stand-in for the engine: no Claude, no API key, nothing happens on the PC. For working on the UI (any OS). It keeps its own settings (`fake-settings.env`, keys only in memory) and never touches your real ones. Try `send ...` (asks for approval), `long ...` (busy until Stop), `timer`, `fail`. |
| `python -m jarvis.app --port 8770` | A fixed port. By default Alfred picks a free one. |

Running it again while it's open just brings the open Alfred to the front.

## The window

- **Home**: your commands, Alfred's replies, each step it takes (`thinking...`, the tools it runs), reminders, and what came from Telegram. Enter sends, Shift+Enter starts a new line, Esc jumps to STOP (then Enter presses it). "Start a fresh conversation" makes Alfred forget the conversation so far.
- **Status** (top right): Ready, Working, Stopping, Starting, Needs your OK (the data notice isn't accepted yet) or Crashed (with a Restart button). Next to it: Telegram's state.
- **STOP** is always on screen. It kills whatever Alfred is doing on the PC (a PowerShell command, computer use, a half-finished action), lets go of the mouse and keyboard, and starts a fresh engine.
- **Approvals**: before Alfred sends, deletes, buys or posts, or another of your PCs wants to act on this one, a dialog shows what it wants to do and where the request came from (this window, Telegram, voice, another PC). Allow or Deny; no answer within the countdown means no. Whoever answers first (the window or Telegram) wins.
- **Data notice**: the first time, the window shows the full notice ([PRIVACY.md](PRIVACY.md)). Alfred doesn't start until you press **I agree**.
- **Settings**: model, effort, computer use, spoken replies, this PC's name; API keys; Telegram on/off and its owner. When Telegram is waiting for an owner and someone messages the bot, Home shows their Telegram ID with a button to make them the owner. Saving restarts Alfred's engine after what it's doing now.
- **Activity**: every action Alfred took on this PC, newest first, with a filter.

## Tray icon, closing and quitting

- The tray icon's tooltip shows Alfred's status. Its menu: **Open Alfred**, **Stop everything**, **Quit**.
- Closing the window keeps Alfred running in the tray (it tells you so the first time), so Telegram and reminders keep working. **Quit** in the tray menu (or in Settings) stops the engine and Telegram and closes Alfred.
- **Ctrl+Alt+Backspace** stops Alfred from any app (Windows). If another app already uses that shortcut, the window says the hotkey is off; use STOP or the tray icon.

## Where settings live

| What | Where |
|---|---|
| Settings (model, effort, PC name, ...) | `%LOCALAPPDATA%\Jarvis\settings.env` |
| API keys and tokens | Windows Credential Manager, as `Alfred/<NAME>` (protected by your Windows sign-in). The window only ever shows the last few characters; it can replace or remove a key but never read it back. |
| Data notice acceptance | `%LOCALAPPDATA%\Jarvis\consent.json` |
| Activity (audit log) | `%LOCALAPPDATA%\Jarvis\audit.jsonl` |
| The running app's port (for a second launch) | `%LOCALAPPDATA%\Jarvis\run\` |

Values in the environment or in `windows-mvp\.env` (from source) still win over saved settings; see `config.lookup`.

## Security of the local window

The app serves its window from `127.0.0.1` on a random port, and only that window can drive Alfred:

- The window first opens a one-time link (`/auth?code=...`, valid for 5 minutes) that sets a per-launch secret in an HttpOnly, SameSite=Strict cookie. Every API call needs that cookie.
- Every API call must also name `127.0.0.1:<port>` or `localhost:<port>` as its host, so a web page that points its own domain at your PC is refused. Calls that change anything need the app's own `Origin` and the header `X-Alfred: 1`, which other websites can't send.
- The page itself is locked down (`Content-Security-Policy: default-src 'self'`, no inline scripts, nothing from the internet), and text from Alfred, Telegram or files is always shown as text, never as HTML.

In `--no-window` mode the cookie lives in your normal browser for that session; prefer the native window.

## For developers

- `jarvis/app/server.py`: the API and auth (`App`), on the shared `jarvis/app/http.py`. Other features (onboarding, voice) add routes to `App.router`.
- `jarvis/app/main.py`: the window, tray, hotkey and single-instance lock. pywebview, pystray, Pillow and the Windows APIs are imported only when used.
- `jarvis/app/ui/`: plain HTML, CSS and JavaScript, no frameworks or CDNs.
- Tests: `tests/test_app_server.py` (runs anywhere, uses `FakeService`). The real engine behind it is tested in `tests/test_service.py`.
