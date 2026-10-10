# Jarvis: Windows MVP (Phase 0)

A voice assistant for one Windows 10/11 PC. It opens apps and works inside them through **UI Automation** (the API screen readers use) and **COM** (Office), so it doesn't need to move your mouse or read screenshots. Claude plans the steps; common commands run instantly without it.

## Start in 2 minutes

1. Download this repo (green **Code** button, then **Download ZIP**) and unzip it, or `git clone` it.
2. Double-click **`windows-mvp\setup.bat`**. It finds or installs Python 3.12, installs the packages and asks for your [Anthropic API key](https://platform.claude.com/settings/keys).
3. Double-click **`windows-mvp\jarvis.bat`**.
4. Press **Enter** and talk, or type a command.

The first voice command downloads the speech model (about 150 MB). With no API key, the simple commands still work.

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

## Use several PCs

One Jarvis can work on all your PCs: "open Spotify on the laptop", "copy build.zip from computer 2 to here and unzip it", "read the Excel numbers on the desktop and put them in Word on the laptop".

1. Install [Tailscale](https://tailscale.com/download) on each PC and sign in with the same account. That puts the PCs on one private, encrypted network that works from anywhere. A home Wi-Fi network works too, but Tailscale also works when the PCs are on different networks.
2. On the PC you **don't** talk to (computer 2), run `jarvis.bat --serve`. It prints two lines, for example:
   ```
   JARVIS_DEVICES=laptop=100.64.0.2:8765
   JARVIS_TOKEN=Xy3...
   ```
   Click **Allow** if Windows Firewall asks.
3. On the PC you talk to (computer 1), paste those two lines into `windows-mvp\.env` and run `jarvis.bat`. It says `Connected to laptop (... tools)`.

For both directions, set `JARVIS_SERVE=1` on each PC, list the other PC in `JARVIS_DEVICES`, and use the same `JARVIS_TOKEN` on both.

| Say | What happens |
|---|---|
| "Open Spotify on the laptop" / "tell computer 2 to pause" | Instant, no AI call: the fast path runs the tool on that PC. Jarvis adds under 1 ms on top of the network time |
| "What's on the laptop's screen?" | Screenshot from the laptop, described here |
| "Copy report.docx from the laptop to my Downloads and open it" | `copy_file`, then `open_app` here |
| "Write a Python script on computer 2 that prints the date, copy it here and run it" | Claude chains PCs: it writes the file on one, moves it, runs it on the other |

**Speed:** each PC keeps one open connection (no reconnect per call), tool lists are fetched once at startup, and calls to different PCs in the same step run at the same time.

**Safety:** every request needs the shared token. Actions that need approval are confirmed on the PC you're talking to. The serving PC refuses them unless the caller says you approved. Don't expose the port to the internet; use Tailscale or your home network. Each PC logs what it ran in its own audit log.

## Modes

```
jarvis.bat            press Enter to talk, or type
jarvis.bat --wake     hands-free: say "Jarvis, ..." any time
jarvis.bat --text     typing only
jarvis.bat --do "open notepad"
jarvis.bat --serve    let your other PCs use this one
jarvis.bat --list-tools
```

Settings live in `windows-mvp\.env` (see `.env.example`): model (default `claude-opus-5-5` at low effort, for speed), speech model size, voice on/off. Server-side refusal fallbacks are on by default (`JARVIS_FALLBACKS=0` turns them off).

## Safety

- It runs as you, in your session. No admin rights, no background service.
- It asks first (y/N in the console) before PowerShell commands and before clicking anything named Send, Delete, Buy, Post, Submit and similar.
- It treats text from windows, web pages and the clipboard as data, not as instructions.
- Every action is logged to `%LOCALAPPDATA%\Jarvis\audit.jsonl`. Ctrl+C stops everything.

## How it works

```
mic -> Whisper (local) -> fast-path router --match--> tool
                              \--no match--> Claude (tool use) -> tools -> spoken reply (Windows voice)
tools: open/close/focus apps, UI Automation (list/click/set/read), Excel/Word COM,
       media keys, volume, clipboard, web, timers, screenshot, PowerShell (asks first)
```

Code: `jarvis/router.py` (instant commands), `jarvis/brain.py` (Claude loop), `jarvis/tools/` (one file per area), `jarvis/remote.py` (other PCs), `jarvis/voice.py`.

## Limits

- Apps running as administrator can't be controlled unless Jarvis also runs as administrator.
- Games and custom-drawn apps expose little to UI Automation. Jarvis falls back to a screenshot.
- Excel and Word tools need Office installed and the file already open.
- Voice uses a simple loudness detector, so a quiet room works best.

Next phases (service, pairing by QR code, phone client) are in the [main README](../README.md#8-build-phases).
