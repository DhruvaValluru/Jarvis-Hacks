"""Jarvis voice: talk to OpenAI's GPT-Live-1 from a browser, like ChatGPT voice mode.

The browser sends its WebRTC offer to POST /session. This server forwards it to
OpenAI's Live API with your API key and returns OpenAI's answer, so the key
never reaches the browser. Audio then flows directly between the browser and
OpenAI.

Run:  python server.py   then open http://localhost:8000
"""

import hmac
import os
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, PlainTextResponse, Response
from openai import (
    APIConnectionError,
    APIStatusError,
    AsyncOpenAI,
    AuthenticationError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
)

HERE = Path(__file__).resolve().parent
KEY_FILE = HERE.parent / "api"  # the key file at the repo root


def load_api_key() -> str:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key and KEY_FILE.exists():
        key = KEY_FILE.read_text().strip()
    if not key:
        raise SystemExit(
            "No OpenAI API key found. Set OPENAI_API_KEY or put the key in the 'api' file at the repo root."
        )
    return key


# Settings you can change with environment variables.
VOICE = os.environ.get("JARVIS_VOICE", "marin")
BRAIN_MODEL = os.environ.get("JARVIS_BRAIN_MODEL", "gpt-5.5")
PASSCODE = os.environ.get("JARVIS_PASSCODE", "")
PORT = int(os.environ.get("PORT", "8000"))

VOICE_INSTRUCTIONS = """\
You are Jarvis, a personal voice assistant. Talk like a sharp, friendly person on a call:
short sentences, natural rhythm, no lists or markdown. Keep answers brief unless asked for detail.
If you're interrupted, stop and listen.
For anything that needs current information, looking something up, calculations, or careful
thinking, hand the task to your backend. While it works, say a few words like "Let me check."
"""

BRAIN_INSTRUCTIONS = """\
You are the reasoning backend for Jarvis, a voice assistant. Your answer will be spoken aloud,
so write plain conversational sentences: no markdown, tables, links, or long lists.
Lead with the answer, keep it short, and search the web when the question needs fresh facts.
"""

SESSION_CONFIG = {
    "model": "gpt-live-1",
    "audio": {"output": {"voice": VOICE}},
    "instructions": VOICE_INSTRUCTIONS,
    # Harder questions go to a text model managed by OpenAI, with web search.
    "delegation": {
        "type": "responses",
        "responses": {
            "model": BRAIN_MODEL,
            "instructions": BRAIN_INSTRUCTIONS,
            "tools": [{"type": "web_search"}],
        },
    },
    # The browser may only end the session; it can't rewrite instructions.
    "client": {
        "data_channel": {
            "allowed_client_events": ["session.close"],
            "allowed_server_events": "all",
        }
    },
}

client = AsyncOpenAI(api_key=load_api_key())


def reason(e: APIStatusError) -> str:
    """OpenAI's human-readable error message, without the raw response dump."""
    if isinstance(e.body, dict) and e.body.get("message"):
        return str(e.body["message"])
    return e.message
app = FastAPI(title="Jarvis voice")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(HERE / "static" / "index.html")


@app.post("/session")
async def create_session(request: Request) -> Response:
    if PASSCODE and not hmac.compare_digest(request.headers.get("x-jarvis-passcode", ""), PASSCODE):
        return PlainTextResponse("Wrong passcode.", status_code=401)

    offer = (await request.body()).decode()
    if not offer.startswith("v="):
        return PlainTextResponse("Expected a WebRTC SDP offer.", status_code=400)

    try:
        live = await client.live.create(
            session=SESSION_CONFIG,
            transport={"type": "webrtc", "sdp": offer},
        )
    except AuthenticationError:
        return PlainTextResponse("OpenAI rejected the API key. Check the key.", status_code=502)
    except PermissionDeniedError as e:
        return PlainTextResponse(
            f"Your OpenAI account can't use gpt-live-1 yet (it may need organization verification): {reason(e)}",
            status_code=502,
        )
    except NotFoundError as e:
        return PlainTextResponse(f"OpenAI couldn't find the model: {reason(e)}", status_code=502)
    except RateLimitError as e:
        return PlainTextResponse(
            f"OpenAI rate limit or out of credits. Check billing: {reason(e)}", status_code=502
        )
    except APIStatusError as e:
        return PlainTextResponse(f"OpenAI error {e.status_code}: {reason(e)}", status_code=502)
    except APIConnectionError:
        return PlainTextResponse("Couldn't reach OpenAI. Check your internet connection.", status_code=502)

    print(f"Live session started: {live.session.id}")
    return Response(content=live.transport.sdp, media_type="application/sdp")


if __name__ == "__main__":
    print(f"Jarvis voice on http://localhost:{PORT}  (voice: {VOICE}, brain: {BRAIN_MODEL})")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
