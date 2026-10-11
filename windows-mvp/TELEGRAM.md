# Jarvis through Photon Spectrum and Telegram

Telegram text or voice note -> Photon Spectrum -> the Windows Jarvis worker ->
Telegram text and an optional AI-generated spoken reply. Keep the Windows PC
awake and logged in while the bridge runs.

## Windows setup

1. Check out `Saatvik-Branch`. Run `windows-mvp\setup.bat`, then
   `windows-mvp\setup-telegram.bat` (requires Node.js 22 or newer).
2. Copy `.env.example` to `.env` if needed. Set your Photon project settings:

   ```dotenv
   SPECTRUM_PROJECT_ID=908385d3-2f06-4c90-92d3-5937397eea69
   SPECTRUM_PROJECT_SECRET=YOUR_PHOTON_PROJECT_SECRET
   TELEGRAM_BOT_TOKEN=YOUR_BOTFATHER_TOKEN
   TELEGRAM_OWNER_ID=
   ```

   Get the Telegram bot token by messaging **@BotFather**, sending `/newbot`,
   and following its prompts. Use a dedicated bot. Keep secrets in `.env`;
   they are ignored by Git. The OpenAI API key and Photon project secret do
   **not** replace the Telegram bot token.
3. Stop the old polling bridge and any other instances using this bot. Run
   `jarvis-telegram.bat`. Spectrum registers the bot's webhook with Photon
   automatically; no public local server or tunnel is needed.
4. Open your bot and send `/start`. With `TELEGRAM_OWNER_ID` blank, the PC
   prints your numeric user ID and runs no PC commands.
5. Stop with Ctrl+C, set `TELEGRAM_OWNER_ID` to that ID, and restart. Send
   `/start` again. The first authorized message starts the Windows worker;
   Jarvis sends its online notice once ready.
6. Send `open notepad`, `volume 30`, `what is running`, or `timer for 10 seconds`.
   Complex requests still use the original `ANTHROPIC_API_KEY` and Claude brain.

Only the configured owner's private Telegram chat can control Windows.
Messages from groups, other users, other platforms and the bot itself are ignored.

The scaffold command from Photon is for starting a new project. This existing
MVP directly uses `spectrum-ts@12.10.1`, `Spectrum({...})`,
`telegram.config({ botToken })`, and `for await (const [space, message] of app.messages)`.
The dependency is already in the lockfile; you do not need to create another
`jarvis` folder. See the [Photon SDK](https://github.com/photon-hq/spectrum-ts).

## Voice now and from the other repo

For Telegram voice notes and spoken replies, set `OPENAI_API_KEY` locally and
restart. No separate voice server or `jarvisvoice` branch is needed. Defaults:
`OPENAI_TRANSCRIBE_MODEL=gpt-4o-mini-transcribe`,
`OPENAI_TTS_MODEL=gpt-4o-mini-tts`, `OPENAI_TTS_VOICE=cedar`. Voice notes are
transcribed directly by OpenAI, shown as `Heard: ...`, and sent to the same
Windows command path as typed messages. The bridge generates
MP3 and sends Spectrum `voice(...)` content. `/audio off` disables spoken replies
for this run; `/audio on` enables them. Replies always include text. Speech is
limited to the first 1,500 characters and OpenAI API billing applies.

To connect your friend's voice implementation, configure:

```dotenv
VOICE_SERVICE_URL=http://127.0.0.1:8000
# Optional authentication if their service requires it:
VOICE_SERVICE_TOKEN=YOUR_SERVICE_TOKEN
```

Restart the bridge after changing settings. With this URL configured, the
service handles both speech and transcription, overriding direct OpenAI voice.
Leave `VOICE_SERVICE_URL` unset to use OpenAI directly. The browser voice app
in `jarvisvoice` only has `/session` and is not a compatible custom service.

Give your friend [VOICE_API.md](VOICE_API.md) for the exact HTTP contract. Their
repo can run separately on the same PC or expose an accessible HTTPS service.
Neither side needs to import the other repo or use MCP.

## Approval and message behavior

Actions requiring permission send `/approve CODE` and `/deny CODE`. Type one
of those commands; voice transcripts cannot approve or deny actions. Unanswered
requests expire after 60 seconds. Transcription runs separately so typed
approval replies can still arrive. Two voice transcriptions may be active at
once; voice notes are limited to 5 MB and 2 minutes.

## Stopping Alfred from Telegram

Send **`/stop`** for a hard stop. The PC kills the Windows worker and everything
it started (a Claude request in flight, PowerShell, a computer-use drag), starts
a fresh worker, and the fresh worker lets go of any mouse button or key that was
still held down. You get "Stopping now." straight away and a "Stopped." message
when Alfred is ready again (a few seconds). Pending approvals and queued commands
are dropped and the conversation is reset. `/stop` must be typed; it is logged
in the PC's audit log as `telegram_stop`.

## In the Alfred desktop app

The app runs the same bridge in parent mode (`ALFRED_BRIDGE_MODE=parent`): it
passes the Telegram settings from Alfred settings, the bridge reads no `.env` and
starts no Python, and Telegram commands run on the app's one engine with the
desktop's commands (`jarvis/service/bridge.py`). Replies and approval requests
for Telegram's commands go back to Telegram, and `/stop` stops the app's engine.
`jarvis-telegram.bat` works as before.

## Privacy

`/start` and `/privacy` show where your data goes: messages pass through
Telegram and Photon, voice notes and spoken replies use OpenAI, and requests,
text Alfred reads and screenshots go to Anthropic. The full notice is
[PRIVACY.md](PRIVACY.md). The person at the PC accepts it before the bridge
starts; `jarvis-telegram.bat` asks if it hasn't been accepted yet.

`/help` lists commands. `/reset` clears the current Claude conversation. Timers
send replies to the owner's chat. Timers and conversation memory are in memory
and do not survive restart.

Spectrum streams can redeliver messages. The bridge records the last 1,000
accepted chat/message IDs in `.telegram-state.spectrum.json` before dispatch.
Repeated IDs are skipped across restart; messages older than two minutes are
also skipped. State is scoped to the project, bot and owner. If the bridge
crashes mid-command, check the PC before explicitly sending the command again.
If recording state fails, that message does not run. Transcription failures ask
for a typed command. Speech or voice delivery failures leave the text available.

## Troubleshooting and checks

- No connection: check all three Spectrum/Telegram credentials and internet
  access. Use a dedicated bot; Spectrum takes over its webhook.
- No ID printed: send a fresh `/start` to your bot, not BotFather.
- No response: verify `TELEGRAM_OWNER_ID`, restart, and send a fresh `/start`.
- Python fails: run `setup.bat`, or set `JARVIS_PYTHON` to the Windows Python
  executable with MVP packages installed.
- No voice: check `OPENAI_API_KEY` and API billing, or your friend's service URL,
  token and endpoint responses. Send `/audio on`.
- The original MVP's limits for locked desktops, elevated apps and Office apply.

```text
cd windows-mvp
python -m pytest -q tests/test_core.py tests/test_telegram.py
cd telegram
npm ci
npm test
```

Automated tests validate real Spectrum content builders and mocked voice
responses without sending messages or spending API credits. Windows CI also
runs the desktop smoke test. Live Telegram pairing needs the bot token and a
running Windows bridge.
