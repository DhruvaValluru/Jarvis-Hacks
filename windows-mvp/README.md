# Alfred: Windows MVP (Phase 0)

The assistant is called **Alfred**. The code, scripts and commands still use the working name `jarvis` (`jarvis.bat`, `python -m jarvis`). Say "Alfred, ..." or "Jarvis, ..." to wake it.

A voice assistant for one Windows 10/11 PC. It opens apps and works inside them through **UI Automation** (the API screen readers use) and **COM** (Office), so it doesn't need to move your mouse or read screenshots. For apps those can't reach (games, canvases, custom-drawn apps) it falls back to **computer use**: Claude looks at screenshots and drives the real mouse and keyboard. Claude plans the steps; common commands run instantly without it.

## Start in 2 minutes

1. Download this repo (green **Code** button, then **Download ZIP**) and unzip it, or `git clone` it. Until computer use is merged, it lives on the `eshaan-work` branch: `git clone -b eshaan-work https://github.com/DhruvaValluru/Jarvis-Hacks.git`. In PowerShell, go to the folder with `cd Jarvis-Hacks\windows-mvp`. Can't find it? Run `Get-ChildItem $HOME -Recurse -Directory -Filter windows-mvp -ErrorAction SilentlyContinue`.
2. Double-click **`windows-mvp\setup.bat`**. It finds or installs Python 3.12, installs the packages and asks for your [Anthropic API key](https://platform.claude.com/settings/keys).
3. Double-click **`windows-mvp\jarvis.bat`**.
4. Press **Enter** and talk, or type a command.

**Testing:** run `test.bat`. Everyone tests against one pinned spec (Python 3.12, exact package versions, fixed settings). See [TESTING.md](TESTING.md).

The first voice command downloads the speech model (about 150 MB). With no API key, the simple commands still work.

## Getting your API key

Jarvis needs an **Anthropic** API key. It starts with `sk-ant-`. Keys from other providers (for example OpenAI's `sk-proj-...`) won't work.

1. Go to [platform.claude.com](https://platform.claude.com) and sign in or create an account.
2. Add credits under **Billing**. The API is pay-as-you-go and separate from a Claude.ai subscription. Setting a monthly spend limit there is a good idea while testing.
3. Open **Settings → API keys** ([direct link](https://platform.claude.com/settings/keys)) and click **Create key**. Name it something like `jarvis-home-pc`, then copy it. It's shown only once.
4. Run `setup.bat` and paste the key when it asks. If you skipped that step, or `.env` already exists, open `windows-mvp\.env` in Notepad and set the line to `ANTHROPIC_API_KEY=sk-ant-...`. No quotes, no spaces.
5. Run `jarvis.bat --text`. The banner should say `Brain: claude-opus-5-5`. If it says `Brain: none (no API key)`, the key wasn't picked up.
6. Test it: type `open notepad and write a haiku about Mondays`. If Jarvis says the key was rejected, re-copy it into `.env`.

Keep the key out of git. `.env` is already in `.gitignore`, so never paste the key into code, docs or chat. If it leaks, delete it on the same page and create a new one.

## First-run checklist

Run `.\jarvis.bat --text`, then try these in order:

| # | Type | Checks |
|---|---|---|
| 1 | `what time is it` | Instant command (no AI) |
| 2 | `open notepad and write a haiku about Mondays` | Claude + UI Automation, no mouse movement |
| 3 | `what's in my notepad?` | Reading app text |
| 4 | `open Paint and draw a red square` | **Computer use**: the mouse moves by itself. Hands off; move the mouse to the top-left corner to stop it |

If something goes wrong, copy the terminal output (the `-> tool {...}` lines show each step) and the last actions from the log: `Get-Content $env:LOCALAPPDATA\Jarvis\audit.jsonl -Tail 20`.

## Telegram + OpenAI

Control this PC from a private Telegram chat using Photon's Spectrum runtime.
Replies are text plus optional OpenAI audio. Run `setup-telegram.bat`,
configure your bot token and owner ID, then start `jarvis-telegram.bat`.
See [TELEGRAM.md](TELEGRAM.md) for Photon credentials, bot creation, pairing and audio setup.
OpenAI directly transcribes incoming voice notes and generates spoken replies.
An optional custom service can replace both through [VOICE_API.md](VOICE_API.md).

## Things to try

| Say or type | What happens |
|---|---|
| "Open Spotify" / "next song" / "volume 30" | Instant (no AI call): Start menu launch, media keys, system volume |
| "Open Spotify and play my focus playlist" | Claude opens Spotify, finds the search box and Play button through UI Automation |
| "Open Notepad and write a haiku about Mondays" | Launches Notepad and sets the text directly |
| "What's in my Word doc?" / "Summarize this window" | Reads the text through UI Automation or COM |
| "Put the Excel numbers into my report as a table" | Reads the Excel range and writes a Word table, no clicking |
| "Remind me in 10 minutes to stretch" | Timer that speaks when it's done |
| "What's on my screen?" | Takes a screenshot and describes it (only when asked) |
| "Open Paint and draw a red square" | Computer use: screenshots plus real mouse and keyboard, because Paint's canvas is invisible to UI Automation |

## Use several PCs

One Jarvis can work on all your PCs: "open Spotify on the laptop", "say dinner's ready on computer 2", "copy build.zip from computer 2 to here and unzip it", "send my clipboard to the laptop".

1. Put the PCs on the same network: the same home Wi-Fi, or [Tailscale](https://tailscale.com/download) on each PC signed in to one account. Tailscale is private and encrypted, and it works when the PCs are in different places.
2. On computer 2, run `jarvis.bat --serve`. It prints a `JARVIS_TOKEN=...` line. Click **Allow** if Windows Firewall asks.
3. Paste that line into `windows-mvp\.env` on computer 1 and run `jarvis.bat`. It finds computer 2 by itself and says `Connected to laptop at 100.64.0.2:8765 (25 tools, 3 ms)`.

You don't type any IP addresses. If discovery can't see a PC (some routers block broadcasts), add the `JARVIS_DEVICES=...` line that `--serve` also prints. To control both PCs from either one, set `JARVIS_SERVE=1` on both and use the same token.

| Say | What happens |
|---|---|
| "Open Spotify on the laptop" / "tell computer 2 to pause" / "say hi on the laptop" | Instant, no AI call: the fast path runs it on that PC |
| "Send my clipboard to the laptop" | Instant clipboard handoff |
| "Copy report.docx from the laptop to my Downloads and open it" | `copy_file` streams the file at full network speed (any size), then opens it here |
| "Remind me in 10 minutes on the laptop" | The reminder comes back and is spoken on the PC you're talking to |
| "What's the laptop doing?" | Answered right away: each PC pushes its active window and whether someone's using it |
| "Build it on computer 2, copy it here and run it" | Claude chains PCs; calls to different PCs in one step run at the same time |

**How it stays fast:**
- Each PC keeps two open connections to every other PC. One is for commands: Jarvis adds under 1 ms on top of the network time.
- The other is a live feed the other PC answers the instant something happens: a message, a new active window, or a change to its tools. It also works as a heartbeat, so an offline PC is spotted right away and commands to it fail in under 2 seconds instead of hanging.
- Files stream as raw bytes on a separate connection, so a big copy never blocks commands.
- Claude already knows what every PC is showing when you ask, which saves a whole round trip.

**Safety:**
- Every request, and every discovery reply, has to prove the shared token. A stranger on your network can't find, control or impersonate your PCs.
- Actions that need approval, including writing files, are confirmed on the PC you're talking to. The serving PC refuses them otherwise.
- Don't expose port 8765 to the internet. Each PC logs what it ran in its own audit log.

## Modes

```
jarvis.bat            press Enter to talk, or type
jarvis.bat --wake     hands-free: say "Jarvis, ..." any time
jarvis.bat --text     typing only
jarvis.bat --do "open notepad"
jarvis.bat --serve    let your other PCs use this one (prints the token)
jarvis.bat --list-tools
```

Settings live in `windows-mvp\.env` (see `.env.example`). The Alfred desktop app keeps them outside the install folder instead: plain settings in `%LOCALAPPDATA%\Jarvis\settings.env`, API keys and tokens in Windows Credential Manager (`Alfred/<NAME>`); a variable set in the environment wins over both, and from source `windows-mvp\.env` does too (`jarvis/config.py`). Settings: model (default `claude-opus-5-5` at low effort, for speed), speech model size, voice on/off. Server-side refusal fallbacks are on by default (`JARVIS_FALLBACKS=0` turns them off). Computer use is on by default (`JARVIS_COMPUTER_USE=0` turns it off).

## Safety

- It runs as you, in your session. No admin rights, no background service.
- It asks first (y/N in the console) before PowerShell commands and before clicking anything named Send, Delete, Buy, Post, Submit and similar, including clicks made through computer use.
- It never types into password fields; you enter passwords yourself ([plan for user-approved fill](../docs/CREDENTIALS_AND_USER_SECURITY.md)).
- During computer use, move the mouse into the **top-left corner** to take control back. It stops by itself on the lock screen and admin (UAC) prompts.
- It treats text from windows, web pages and the clipboard as data, not as instructions.
- Every action is logged to `%LOCALAPPDATA%\Jarvis\audit.jsonl`. Ctrl+C stops everything.
- Computer-use screenshots are sent to Claude to decide the next step; turn it off with `JARVIS_COMPUTER_USE=0` if you'd rather not.
- From Telegram, **`/stop`** is a hard stop: it kills whatever Alfred is doing and lets go of the mouse and keyboard.

## Privacy and terms

Setup shows a short data notice and asks you to type `agree`; Alfred doesn't run until you do. It says what goes to Anthropic (requests, window text, screenshots), OpenAI (Telegram voice notes and spoken replies) and Telegram/Photon (your chat), and what stays on the PC. Full text: [PRIVACY.md](PRIVACY.md). Read it again with `jarvis.bat --terms`.

## How it works

```
mic -> Whisper (local) -> fast-path router --match--> tool
                              \--no match--> Claude (tool use) -> tools -> spoken reply (Windows voice)
tools: open/close/focus apps, UI Automation (list/click/set/read), Excel/Word COM,
       media keys, volume, clipboard, web, timers, screenshot, PowerShell (asks first)
       + computer use (fallback): screenshot, click, type, key, scroll, zoom
```

Code: `jarvis/router.py` (instant commands), `jarvis/brain.py` (Claude loop), `jarvis/tools/` (one file per area; computer use is `computer.py` + `wininput.py`), `jarvis/remote.py` + `jarvis/discovery.py` (other PCs), `jarvis/voice.py`. Plan: [docs/COMPUTER_USE_PLAN.md](../docs/COMPUTER_USE_PLAN.md).

## Limits

- Apps running as administrator can't be controlled unless Jarvis also runs as administrator.
- Games and custom-drawn apps expose little to UI Automation. Jarvis falls back to computer use, which is slower and works on the primary monitor only.
- Excel and Word tools need Office installed and the file already open.
- Voice uses a simple loudness detector, so a quiet room works best.

Next phases (service, pairing by QR code, phone client) are in the [main README](../README.md#8-build-phases).
