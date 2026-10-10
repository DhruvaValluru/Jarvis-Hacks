# Control the Windows MVP from Telegram

Send a private Telegram message to your Jarvis bot. The Windows PC runs the
command and sends a text reply, plus an MP3 when OpenAI is configured.
Commands run one at a time in the logged-in Windows user session.

## Setup on your Windows PC

1. Check out `Saatvik-Branch` and run `windows-mvp\setup.bat` for the Python MVP.
2. Install **Node.js 22 or newer** from https://nodejs.org. Run
   `windows-mvp\setup-telegram.bat` to install the Telegram dependencies.
3. In Telegram, message the official **@BotFather**, send `/newbot`, and follow
   its prompts. Use a new, dedicated bot; do not reuse a bot configured with a
   webhook or another running bridge. Copy the bot token into
   `windows-mvp\.env` as `TELEGRAM_BOT_TOKEN=...`. Keep tokens out of Git and chat.
4. Leave `TELEGRAM_OWNER_ID` blank and run `jarvis-telegram.bat`. Open your bot
   in Telegram and press Start (or send `/start`). The PC console prints your
   numeric user ID. In this discovery mode, **no Windows commands run**.
5. Close the bridge with Ctrl+C. Set `TELEGRAM_OWNER_ID` to that numeric ID in
   `.env`, then run `jarvis-telegram.bat` again. It texts you when Jarvis is online.
6. Try `open notepad`, `volume 30`, `what is running`, or `timer for 10 seconds`.
   Complex requests need your `ANTHROPIC_API_KEY`, just like the original MVP.

Only the configured user's **private chat** can control the PC. Groups,
channels, other users, and bot messages are ignored. The bridge does not expose
an HTTP port. Keep the Windows PC awake and logged in, with the bridge running.
The first owner-authorized command must be sent after the worker is online.

## OpenAI audio

Add `OPENAI_API_KEY` to the same `.env` file and restart. Optionally set
`OPENAI_TTS_VOICE` (default `cedar`) and `OPENAI_TTS_MODEL` (default
`gpt-4o-mini-tts`). Replies arrive as text plus an MP3 audio message using an
AI-generated voice. Speech generation is limited to the first 1,500 characters
of each reply; the complete text is still sent. OpenAI API billing applies.
Remove any old `ELEVENLABS_*` settings; the bridge no longer reads them.

The request uses OpenAI's [speech endpoint](https://developers.openai.com/api/docs/guides/text-to-speech).
This handles outgoing spoken replies. The existing Claude brain and local
Windows microphone mode keep their current providers.

Send `/audio off` for text-only replies or `/audio on` to enable audio for this
run. If synthesis or audio upload fails, the text reply remains available.
Incoming voice notes are not transcribed in this version; send typed commands.

## Approvals and ongoing replies

For actions the MVP marks as needing permission, Jarvis sends the action and a
unique code. Send `/approve CODE` or `/deny CODE`. No console input is needed.
Unanswered requests expire after 60 seconds and are declined. Codes are valid
for one pending action only. `/reset` clears the current Claude conversation;
`/help` lists commands. Ctrl+C on the PC stops the bridge and its Windows worker.

Timers reply to the same owner's chat. Timers and conversation memory remain
in memory and do not survive restart. Received update IDs are stored in the
ignored `.telegram-state.json` file before dispatch, preventing a restart from
executing an already accepted command twice. If the bridge crashes mid-command,
check whether the action completed before sending it again. Messages older
than two minutes are skipped rather than executing old commands on reconnect.

## Architecture

```text
Telegram private chat
  -> Photon's @photon-ai/telegram-ts SDK (long polling)
  -> local Python jarvis.telegram_worker (JSON lines over stdin/stdout)
  -> existing Jarvis.handle -> router/Claude -> Windows tools
  -> Jarvis.reply -> Telegram text + optional OpenAI MP3
```

This integration uses the **published Photon Telegram SDK**, pinned to
`@photon-ai/telegram-ts@10.1.1`. It calls Telegram's Bot API directly. It does
not use Spectrum Cloud, a Photon project/secret, MCP, or a public webhook.
If a sponsor track specifically requires the Spectrum runtime, this SDK-only
integration should not be represented as fulfilling that requirement.

## Troubleshooting

- No user ID printed: ensure you opened your bot, not BotFather, and sent a
  fresh `/start`. Check the token and internet connection.
- No reply after configuring: verify the numeric owner ID, restart the bridge,
  and send a fresh command from your private chat.
- Worker will not start: run `setup.bat` first. Advanced users can set
  `JARVIS_PYTHON` to a Python executable with the MVP packages installed.
- Bot already has a webhook / another instance: use a dedicated new bot and
  run one bridge. This launcher does not delete another integration's webhook.
- No audio: check the OpenAI key, API billing, voice/model access, and
  `/audio on`. Text delivery does not require OpenAI.
- Elevated apps, a locked desktop, and Office requirements have the same limits
  as the original Windows MVP.

## Tests

```text
cd windows-mvp
python -m pytest -q tests/test_core.py tests/test_telegram.py
cd telegram
npm ci
npm test
```

The Telegram tests use the real published SDK with mocked network responses,
including multipart MP3 upload. They do not spend credits or send real messages.
The Windows workflow also runs the original desktop smoke test. A live
Telegram/OpenAI demo requires your locally configured credentials.
