# Alfred desktop app

The Alfred app is a normal window plus a tray icon. You type what you want, watch what Alfred is doing, answer its questions (Allow or Deny), and press **STOP** whenever you like. Telegram runs through the same app, so there is one conversation and one Stop for everything.

To install it like any other Windows app, use the installer: see [INSTALL.md](INSTALL.md). Or run it from source.

## Run it from source

```bat
cd windows-mvp
setup.bat
.venv\Scripts\python -m jarvis.app
```

- `pywebview` gives Alfred its own window (it uses Microsoft Edge WebView2, which Windows 11 already has). Without it, Alfred opens in your default browser instead. If the PC has no WebView2 Runtime, Alfred says so and uses the browser too.
- `pystray` adds the tray icon. Without it there is no tray icon, so closing the window quits Alfred: it never keeps running where you can't see it.
- Both are in `requirements.lock.txt` (`requirements-desktop.txt`, test spec v3), so `setup.bat` installs them.

Options:

| Command | What it does |
|---|---|
| `python -m jarvis.app` | The app. |
| `python -m jarvis.app --no-window` | Use your default browser instead of Alfred's window. The console prints a link that works once, in case the browser doesn't open. |
| `python -m jarvis.app --fake-core` | A scripted stand-in for the engine: no Claude, no API key, nothing happens on the PC. For working on the UI (any OS). It keeps its own settings (`fake-settings.env`, keys only in memory) and its own setup progress (`fake-onboarding.json`); its "Start Alfred when I sign in" only pretends. It never touches your real ones. Try `send ...` (asks for approval), `long ...` (busy until Stop), `timer`, `fail`. |
| `python -m jarvis.app --port 8770` | A fixed port. By default Alfred picks a free one. |

Running it again while it's open just brings the open Alfred to the front.

## First run: the setup

The first time, Alfred opens its setup instead of Home, and keeps opening it (where you left off) until you press **Done** on the last step. Optional steps have **Skip**; once the required ones are done, **Skip the rest** jumps to the end. Every step can be opened again later from **Settings → Setup**.

An Alfred from before the setup existed (run from source) goes through it once too, on purpose: the notice it already accepted shows as accepted, step 3 imports its old `windows-mvp\.env` (the installed app won't read that file), and the Safety step shows the new ways to stop Alfred. Everything else can be skipped.

| Step | What happens |
|---|---|
| 1. Welcome | What Alfred is, and a quick look at this PC: Windows version, microphone, Office, Tailscale. Only an unsupported Windows (older than Windows 10 1809) stops the setup; the rest is information. |
| 2. Your data | The data notice ([PRIVACY.md](PRIVACY.md)). **I agree** records it and starts Alfred. Required. |
| 3. Old settings | Only when an old `windows-mvp\.env` is next to Alfred, or you pick one (Claude step: "Bring your old settings"). Lists the names, with Alfred's keys shown only by their last characters, and imports them (keys to Credential Manager). Other names that look secret (`HF_TOKEN`, `..._PASSWORD`) are hidden and left out, because plain settings are stored as text. The old file isn't changed. |
| 4. Claude | Paste your Anthropic API key. Alfred checks it with a free call (list one model): rejected key, key without permission and no internet each get their own message. Skip it and Alfred only understands simple commands. |
| 5. Safety | Computer use on or off (on by default: Alfred may use the mouse and keyboard, and screenshots go to Anthropic while it does), and every way to stop Alfred: STOP, the tray icon, Ctrl+Alt+Backspace, /stop in Telegram. |
| 6. Voice | Pick a microphone, download the local speech model (faster-whisper `base.en`, about 145 MB, into `%LOCALAPPDATA%\Jarvis\models`, with progress and Cancel), and try "what time is it" once Alfred's engine can listen (until then the setup says so). |
| 7. OpenAI | Optional key for Telegram voice notes and spoken replies, checked with a free call. |
| 8. Telegram | The bot token (checked with Telegram: you see your bot's @name and link), the Photon Spectrum secret, then **Show a code**: send the 6-digit code to your bot from your own Telegram account and that account becomes the owner. Messages from anyone else are ignored; the code works once, for 10 minutes, and five wrong codes end it. While the code is on screen the bot runs without an owner, but nothing is saved until the code arrives: cancelling, expiry, or Alfred closing leave your previous owner, and Telegram goes back to how it was. |
| 9. Other PCs | **Make a pairing code** on your first PC (shown once, with Copy) or paste it on the others (**Join**); this PC's name; **Let my other PCs use this one** (off by default; Windows Firewall may ask, allow private networks only). |
| 10. Finish | **Start Alfred when I sign in** (off by default; Settings shows it and turns it off again), **Try it** sends "open notepad" and shows the reply, **Done** opens Home. |

## The window

- **Home**: your commands, Alfred's replies, each step it takes (`thinking...`, the tools it runs), reminders, and what came from Telegram. Enter sends, Shift+Enter starts a new line, Esc jumps to STOP (then Enter presses it). "Start a fresh conversation" makes Alfred forget the conversation so far.
- **Status** (top right): Ready, Working, Stopping, Starting, Needs your OK (the data notice isn't accepted yet) or Crashed (a banner says why, with a Restart button). Next to it: Telegram's state.
- **STOP** is always on screen. It kills whatever Alfred is doing on the PC (a PowerShell command, computer use, a half-finished action), lets go of the mouse and keyboard, and starts a fresh engine.
- **Approvals**: before Alfred sends, deletes, buys or posts, or another of your PCs wants to act on this one, a dialog shows what it wants to do and where the request came from (this window, Telegram, voice, another PC). Allow or Deny; no answer within the countdown means no. Whoever answers first (the window or Telegram) wins.
- **Data notice**: the first time, the window shows the full notice ([PRIVACY.md](PRIVACY.md)). Alfred doesn't start until you press **I agree**.
- **Settings**: model, effort, computer use, spoken replies, this PC's name; API keys; Telegram on/off and its owner. When Telegram is waiting for an owner and someone messages the bot, Home says so (once) and points to **Settings → Setup → Telegram**, which links your own account with a code; nobody becomes the owner just by messaging the bot. Saving restarts Alfred's engine after what it's doing now. **Setup** opens any step of the first-run setup again and turns "Start Alfred when I sign in" on or off.
- **Activity**: every action Alfred took on this PC, newest first, with a filter.

## Tray icon, closing and quitting

- The tray icon's tooltip shows Alfred's status. Its menu: **Open Alfred**, **Stop everything**, **Quit**.
- Closing the window keeps Alfred running in the tray (it tells you so the first time), so Telegram and reminders keep working. **Quit** in the tray menu (or in Settings) stops the engine and Telegram and closes Alfred. When Windows signs out, restarts or shuts down, Alfred quits instead of holding it up.
- **Ctrl+Alt+Backspace** stops Alfred from any app (Windows). If another app already uses that shortcut, the window says the hotkey is off; use STOP or the tray icon.

## Where settings live

| What | Where |
|---|---|
| Settings (model, effort, PC name, ...) | `%LOCALAPPDATA%\Jarvis\settings.env` |
| API keys and tokens | Windows Credential Manager, as `Alfred/<NAME>` (protected by your Windows sign-in). The window only ever shows the last few characters; it can replace or remove a key but never read it back. |
| Data notice acceptance | `%LOCALAPPDATA%\Jarvis\consent.json` |
| Activity (audit log) | `%LOCALAPPDATA%\Jarvis\audit.jsonl` |
| What the installed app prints (it has no console) | `%LOCALAPPDATA%\Jarvis\logs\alfred.log` (the run before: `alfred.log.1`) |
| The running app's port (for a second launch) | `%LOCALAPPDATA%\Jarvis\run\` |
| How far the setup got | `%LOCALAPPDATA%\Jarvis\onboarding.json` (delete it to see the setup again) |
| The speech model | `%LOCALAPPDATA%\Jarvis\models\` |
| Start when you sign in | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, value `Alfred` (also in Task Manager's Startup apps) |

Values in the environment or in `windows-mvp\.env` (from source) still win over saved settings; see `config.lookup`.

## Security of the local window

The app serves its window from `127.0.0.1` on a random port, and only that window can drive Alfred:

- The window first opens a one-time link (`/auth?code=...`, valid for 5 minutes). It answers with the page and a per-launch key, which the page keeps in `sessionStorage` and sends as the `X-Alfred` header on every API call. Browsers keep `sessionStorage` per origin, port included, so no other page can read it. Alfred doesn't use a cookie for this, because browsers send `127.0.0.1` cookies to every port, so any other local server the browser visits would receive it.
- Every request must also name `127.0.0.1:<port>` or `localhost:<port>` as its host, so a web page that points its own domain at your PC is refused. Calls that change anything also need the app's own `Origin`. Other websites can't send the `X-Alfred` header (Alfred never answers CORS preflights).
- The page itself is locked down (`Content-Security-Policy: default-src 'self'`, no inline scripts, nothing from the internet), and text from Alfred, Telegram or files is always shown as text, never as HTML.

In `--no-window` mode the key lives in that browser tab until you close it; prefer the native window. A tab you aren't looking at disconnects after 10 seconds (browsers allow only about six connections per server) and catches up when you come back to it.

## For developers

- `jarvis/app/server.py`: the API and auth (`App`), on the shared `jarvis/app/http.py`. Other features (onboarding, voice) add routes to `App.router`.
- `jarvis/app/main.py`: the window, tray, hotkey and single-instance lock. pywebview, pystray, Pillow and the Windows APIs are imported only when used.
- `jarvis/app/ui/`: plain HTML, CSS and JavaScript, no frameworks or CDNs.
- `jarvis/app/ui/theme/aero.css`: the Frutiger Aero design system (tokens, glass panels, glossy buttons, STOP, inputs, switches, progress, steps, callouts; light and dark; reduced motion and Windows contrast themes). The setup uses it; the main window can adopt it.
- `jarvis/onboarding/`: the setup's API (`/api/onboarding/*`, added by `onboarding.register` in `server.py`), its state file, key checks, speech model download, Telegram linking and start-at-sign-in. The pages are `jarvis/app/ui/onboarding/`; `settings-card.js` is the Setup card in Settings.
- Tests: `tests/test_app_server.py` and `tests/test_onboarding.py` (run anywhere, use `FakeService`). The real engine behind it is tested in `tests/test_service.py`.
- The installed app: `packaging/` builds `Alfred.exe` (this app, windowed) and `alfred-cli.exe` (the engine `AlfredService` starts when frozen, the self-test `--selftest` in `jarvis/selftest.py`) and the installer; see [INSTALL.md](INSTALL.md#build-it-yourself).
