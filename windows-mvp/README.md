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

## Modes

```
jarvis.bat            press Enter to talk, or type
jarvis.bat --wake     hands-free: say "Jarvis, ..." any time
jarvis.bat --text     typing only
jarvis.bat --do "open notepad"
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

Code: `jarvis/router.py` (instant commands), `jarvis/brain.py` (Claude loop), `jarvis/tools/` (one file per area), `jarvis/voice.py`.

## Limits

- Apps running as administrator can't be controlled unless Jarvis also runs as administrator.
- Games and custom-drawn apps expose little to UI Automation. Jarvis falls back to a screenshot.
- Excel and Word tools need Office installed and the file already open.
- Voice uses a simple loudness detector, so a quiet room works best.

Next phases (service, cross-PC mesh, phone client) are in the [main README](../README.md#8-build-phases).
