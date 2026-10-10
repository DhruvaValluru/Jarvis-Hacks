# Jarvis voice

Talk to Jarvis the way you talk to ChatGPT voice mode: tap the circle, talk, interrupt whenever you like. It uses OpenAI's **GPT-Live-1**, the model behind ChatGPT's voice, and hands harder questions to a text model with web search.

```
Browser (mic + speaker) ──audio──▶ OpenAI GPT-Live-1 ──hard questions──▶ text model + web search
        │  ▲
  offer │  │ answer          (your API key stays here)
        ▼  │
     server.py
```

The browser asks `server.py` to start a session. The server adds your API key, opens the session with OpenAI, and passes the connection details back. After that, audio goes straight between your browser and OpenAI.

## Run it on your computer

You need Python 3.10 or newer ([python.org](https://www.python.org/downloads/); on Windows, tick "Add python.exe to PATH").

- **Windows:** double-click `start.bat`.
- **Mac:** open Terminal in this folder and run `sh start.sh`.

The first run installs what it needs, which takes about a minute. The script then opens **http://localhost:8000**. Tap the circle and allow the microphone. Use headphones if Jarvis hears itself through your speakers. To stop, close the window or press Ctrl+C.

The server reads your key from the `OPENAI_API_KEY` environment variable. If that isn't set, it reads a file named `api` next to `server.py` or at the repo root.

Manual setup, if you prefer:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python server.py --open
```

## Use it from your phone

Browsers only allow the microphone on `https://` addresses or on `localhost`. The easiest way to get an `https://` link to your computer is a free Cloudflare quick tunnel:

1. Install `cloudflared`. On a Mac: `brew install cloudflared`. On Windows: `winget install --id Cloudflare.cloudflared`.
2. Start the server **with a passcode**, because anyone with the link could otherwise use your OpenAI credits:
   ```bash
   JARVIS_PASSCODE=pick-something python server.py
   ```
   On Windows PowerShell: `$env:JARVIS_PASSCODE="pick-something"; python server.py`
3. In a second terminal, run `cloudflared tunnel --url http://localhost:8000`.
4. Open the `https://….trycloudflare.com` link it prints on your phone. Enter the passcode once; the page remembers it.

Your computer has to stay on with both commands running. The link changes each time you restart the tunnel.

## Settings

Set these as environment variables before `python server.py`:

| Variable | Default | What it does |
|---|---|---|
| `JARVIS_VOICE` | `marin` | Jarvis's voice: alloy, ash, ballad, beacon, bossa, cedar, cinder, coral, delta, echo, gleam, marin, meridian, quartz, ripple, sage, shimmer, stone, tempo, verse, vesper, willow |
| `JARVIS_BRAIN_MODEL` | `gpt-5.5` | The text model that handles questions GPT-Live hands off |
| `JARVIS_PASSCODE` | none | Required passcode for starting a conversation |
| `PORT` | `8000` | Port the server listens on |

Jarvis's personality is in `VOICE_INSTRUCTIONS` and `BRAIN_INSTRUCTIONS` in `server.py`.

## Cost

GPT-Live-1 costs about $0.05 per minute of conversation, billed per second. Questions handed to the text model, and its web searches, are billed separately on your OpenAI account.

## If something goes wrong

The page shows the reason under the circle:

| Message | Fix |
|---|---|
| OpenAI rejected the API key | The key is wrong or was deleted. Make a new one at platform.openai.com. |
| Can't use gpt-live-1 yet | Verify your organization in OpenAI's settings, then make a new key. |
| Rate limit or out of credits | Add credits under Billing. |
| Microphone blocked | Allow the mic for the page in your browser's site settings. |
| Mic only works on localhost or https | Use the tunnel steps above for your phone. |
