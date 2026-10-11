# Alfred: data and terms notice

*Version 2026-10-11. Plain-language notice for people who use Alfred. Have it reviewed before any public release; it is not legal advice.*

Alfred is the assistant in this repository. The code and commands still use the name `jarvis` (`jarvis.bat`, `python -m jarvis`). Everyone accepts this notice once per PC: `setup.bat` asks for it, and Alfred won't start (console or Telegram) until it's accepted. Read it again any time with `jarvis.bat --terms`. When this notice changes, its version changes and everyone is asked again.

## What Alfred can do on your PC

- It runs as you, in your Windows session, without admin rights.
- It opens apps, reads and changes text in them (UI Automation, Office), and can **move your mouse and type on your keyboard** (computer use), including when you text it from Telegram and nobody is at the PC.
- It asks before clicking anything that sends, deletes, buys, posts or submits, and before running PowerShell. When you're on Telegram, it asks there (`/approve CODE` or `/deny CODE`).
- It never types passwords, payment details or one-time codes, and it stops at the lock screen and admin (UAC) prompts.
- **It can make mistakes.** Check anything important, and don't ask it to do something you wouldn't want done on your behalf.

## Where your data goes

| Who | What they receive | When |
|---|---|---|
| **Anthropic** (Claude API) | What you ask; text Alfred reads from your windows, documents and clipboard; the active window's title; **screenshots of your screen** | Every request that isn't a simple built-in command. Screenshots only when computer use runs or you ask Alfred to look. |
| **OpenAI** | Your Telegram voice notes (to turn them into text) and the text of Alfred's replies (to make spoken replies) | Only if `OPENAI_API_KEY` is set and you use Telegram voice or spoken replies (`/audio off` turns replies off) |
| **OpenAI** (voice mode, GPT-Live-1) | Your **microphone audio** and what you say (as text), and the text of Alfred's replies (to say them) | Only while voice mode is on in the Alfred window (see below) |
| **Telegram** and **Photon Spectrum** | Your Telegram messages and voice notes, and Alfred's replies, approval requests and spoken replies | Only if you set up the Telegram bridge |
| **Stays on your PC** | Microphone audio outside voice mode (speech is recognised locally by Whisper), Windows' spoken voice, settings and keys in `windows-mvp\.env`, the action log `%LOCALAPPDATA%\Jarvis\audit.jsonl` | Always |

Each company handles what it receives under its own terms and privacy policy. Read Anthropic's, OpenAI's, Telegram's and Photon's policies if you need the details.

## Voice mode

The microphone button in the Alfred window starts voice mode: you talk with Alfred like on a phone call, through OpenAI's live voice model (GPT-Live-1).

- **While it's on**, your microphone audio goes to OpenAI, which turns it into text and speaks back. What you ask is handed to Alfred on your PC, which works as usual (through Anthropic, as above), and the text of Alfred's replies goes to OpenAI to be spoken.
- **Only while you have it on.** It starts when you press the microphone button and ends when you press it again, press STOP (or any other Stop), close the window, or reload it. Nothing is sent before you press the button or after it ends.
- **Not kept for later use:** Alfred asks OpenAI not to store voice sessions (`store: false`: no forking them or downloading recordings later). OpenAI still handles the audio and text under its own terms and policies.
- **Voice can't approve anything.** When Alfred needs your OK, the dialog appears on screen and you answer there.
- Your OpenAI key stays in Alfred's settings on this PC; the window itself never gets it.

Simple commands handled by the built-in router ("open notepad", "volume 30", "next song") don't call Claude at all.

## Staying in control

- **Stop:** press Ctrl+C in the Alfred window, move the mouse into the top-left corner during computer use, or send **`/stop`** in Telegram. `/stop` is a hard stop: it kills whatever Alfred is doing on the PC, lets go of the mouse and keyboard, and resets the conversation.
- **Turn features off:** `JARVIS_COMPUTER_USE=0` (no screenshots or mouse/keyboard control), no `OPENAI_API_KEY` (no OpenAI, and no voice mode), don't run `jarvis-telegram.bat` (no Telegram).
- **See what happened:** every action is in `%LOCALAPPDATA%\Jarvis\audit.jsonl`.
- **Withdraw:** close Alfred and delete `%LOCALAPPDATA%\Jarvis\` (your acceptance and the action log) and `windows-mvp\.env` (keys). Revoke the API keys and the Telegram bot token on the providers' sites if you no longer use them.

## Who is responsible

You choose what Alfred does on your PC and you're responsible for those requests. Keep your API keys and Telegram bot token private. Someone with your API keys could run up charges; someone with the bot token could read or interfere with the bot's messages. Alfred only obeys the Telegram account set in `TELEGRAM_OWNER_ID`. If a key or token leaks, revoke it and make a new one.
