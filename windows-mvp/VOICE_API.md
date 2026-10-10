# Voice service handoff

Telegram now uses OpenAI directly for transcription and speech when
`VOICE_SERVICE_URL` is unset. This contract is only needed for a custom service.
The Telegram side is ready in `windows-mvp/telegram/voice.mjs`. Implement these
two routes in the other repo, run the server, and set `VOICE_SERVICE_URL` on the
Windows PC. The service can use OpenAI or any speech engine internally.

## POST /speech

Request: `Content-Type: application/json`

```json
{ "text": "Opened Notepad." }
```

Success: HTTP 200, `Content-Type: audio/mpeg`, **raw MP3 bytes**. Do not return
base64, a URL, or JSON. The bridge sends the audio using Spectrum's `voice`
builder with MIME `audio/mpeg` and filename `jarvis.mp3`. At most 1,500 text
characters are sent. Users are told that spoken replies are AI-generated.

## POST /transcribe

Request: `multipart/form-data`, with an uploaded file field named **audio**.
The bridge passes the original bytes and MIME type from Telegram (typically
`audio/ogg` voice notes, but audio attachments can have other audio MIME types).
Support OGG/Opus and MP3. Do not require a particular filename. If calling
OpenAI transcription, map the incoming `audio` field to OpenAI's `file` field.

Success: HTTP 200, `Content-Type: application/json`

```json
{ "text": "open notepad" }
```

Return plain recognized speech; the service should not execute commands or
invent actions. The Windows worker performs commands and asks for approvals.
The bridge shows `Heard: ...` and routes the transcript through the existing
owner-authorized command path. Approval codes must be typed.

## Shared rules

- Optional `Authorization: Bearer ...` comes from `VOICE_SERVICE_TOKEN`.
- Both requests have a 30-second timeout. Return non-2xx status on failure.
- The bridge permits two simultaneous transcription requests, at most 5 MB
  each and 2 minutes of audio when duration is available.
- `VOICE_SERVICE_URL` may include a base path: `https://host/api` becomes
  `https://host/api/speech` and `https://host/api/transcribe`.
- Store the OpenAI API key in your service's environment when using it there.
  The Telegram adapter sends only the service token, text, and audio; it does
  not forward the OpenAI key or Photon project secret.
- Changing the service URL/token requires restarting the Telegram bridge.

Examples against a locally running service:

```text
curl -X POST http://127.0.0.1:8000/speech -H "Content-Type: application/json" -d "{\"text\":\"Jarvis is ready.\"}" --output reply.mp3
curl -X POST http://127.0.0.1:8000/transcribe -F "audio=@voice.ogg"
```

Text chat continues to work if speech generation fails. Failed transcription
asks for a typed command and does not execute anything.
