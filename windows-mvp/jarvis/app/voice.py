"""Voice mode in Alfred's window: talk to Alfred like a phone call, through OpenAI's live voice model.

The page connects to OpenAI's Live API over WebRTC: the microphone goes up, the model's voice comes
back, and a data channel ("oai-events") carries transcripts and delegations. The page sends its SDP
offer to POST /api/voice/session; this server starts the session with the OpenAI key from Alfred's
settings (it never reaches the page) and returns OpenAI's SDP answer. Audio then flows between the
page and OpenAI directly.

The live model only talks. It hands everything the user wants done or found out to our application
(delegation type "client"), and the page posts what the user said to POST /api/voice/delegate. That
runs it on Alfred's engine like a typed command, with source "voice", and returns Alfred's reply for
the model to say. There is no second brain. Approvals stay on screen: voice can't approve anything.

Routes (behind the window's sign-in, like every /api route):
  GET  /api/voice/status                    available, or why not (no consent, no OpenAI key)
  POST /api/voice/session  application/sdp  the page's offer; answers with OpenAI's SDP answer
  POST /api/voice/delegate {delegation_id, text}  runs Alfred and waits; answers {reply, ...}
"""

import json
import re
import urllib.request

from .. import config, consent
from ..onboarding import validators
from ..service import NotReady, QueueFull
from .http import HTTPError, Request, Response

LIVE_SESSIONS = "https://api.openai.com/v1/live/sessions"
DEFAULT_MODEL = "gpt-live-1"
DEFAULT_VOICE = "marin"
DEFAULTS = {"OPENAI_LIVE_MODEL": DEFAULT_MODEL, "OPENAI_LIVE_VOICE": DEFAULT_VOICE}
KEYS = tuple(DEFAULTS)  # only voice mode reads these: changing them restarts nothing
SESSION_TIMEOUT = 20  # seconds for OpenAI to start a session
DELEGATE_TIMEOUT = 240  # seconds a voice request may take before the page hears "still working"
SPOKEN_MAX = 1500  # characters of a reply the live model gets (session.commentary.append takes 500 tokens)
MORE = " The rest is in the Alfred window."
# The only events the page may send on the data channel. It can't change the instructions or the session.
CLIENT_EVENTS = ("session.thinking.append", "session.commentary.append", "session.close")

INSTRUCTIONS = """\
You are the voice of Alfred, a personal assistant on the user's Windows PC. You talk; Alfred does the work.

How to talk:
- Sound like a calm, friendly person on a call. One or two short sentences at a time.
- Plain speech only: no lists, markdown, links or code.
- If the user interrupts, stop and listen.

Hand things to Alfred:
- Delegate every request to do something on the PC (open, close, type, play, send, delete, remind,
  change a setting), to find or check anything (files, email, calendar, windows, the screen, the web),
  and every question that needs facts, current information or a calculation.
- Answer only greetings, small talk and questions about what you heard yourself. When in doubt, delegate.
- Alfred hears only what the user said, so delegate once the user has finished the whole request.
- While Alfred works, say a few words, like "On it" or "Let me check", then wait for its answer.
- Never say you did or found something yourself, and never guess what Alfred will find.

When Alfred answers:
- Tell the user what Alfred said, briefly and naturally. Keep names, numbers and times exactly as given.
- If Alfred is waiting for approval, ask the user to choose Allow or Deny on screen. Nobody can approve
  by voice.
- If Alfred couldn't do it, say so plainly.
"""


class VoiceMode:
    def __init__(self, service, store, *, delegate_timeout: float = DELEGATE_TIMEOUT):
        self.service, self.store = service, store
        self.delegate_timeout = delegate_timeout

    def add_routes(self, router) -> None:
        router.add("GET", "/api/voice/status", lambda req: Response.json(self.status()))
        router.add("POST", "/api/voice/session", self.session)
        router.add("POST", "/api/voice/delegate", self.delegate)

    # --- availability -----------------------------------------------------------
    def settings(self) -> dict:
        """The OpenAI key, model and voice, from the same places as every other setting (config.lookup)."""
        get = config.lookup(self.store)
        return {"key": validators.clean(get("OPENAI_API_KEY")),
                "model": get("OPENAI_LIVE_MODEL") or DEFAULT_MODEL, "voice": get("OPENAI_LIVE_VOICE") or DEFAULT_VOICE}

    def status(self, found: dict | None = None) -> dict:
        found = found or self.settings()
        if not consent.accepted():
            return {"available": False, "reason": "consent",
                    "message": "Accept Alfred's data notice first. Voice mode sends your voice to OpenAI."}
        if not found["key"]:
            return {"available": False, "reason": "key",
                    "message": "Voice mode needs an OpenAI API key. Add one in Settings, under API keys."}
        return {"available": True, "reason": "", "message": "", "model": found["model"], "voice": found["voice"]}

    # --- the live session ---------------------------------------------------------
    def session_config(self, model: str, voice: str) -> dict:
        return {
            "model": model,
            "audio": {"output": {"voice": voice}},
            "instructions": INSTRUCTIONS,
            "delegation": {"type": "client"},
            "client": {"data_channel": {"allowed_client_events": list(CLIENT_EVENTS), "allowed_server_events": "all"}},
            "store": False,  # nothing kept at OpenAI for forking or recordings
        }

    def session(self, req: Request) -> Response:
        """Start a live session with the page's SDP offer and return OpenAI's SDP answer."""
        found = self.settings()
        ready = self.status(found)
        if not ready["available"]:
            raise HTTPError(409, ready["message"])
        if not req.header("Content-Type").lower().startswith("application/sdp"):
            raise HTTPError(415, "Send the WebRTC offer as application/sdp.")
        try:
            offer = req.body.decode("utf-8")
        except UnicodeDecodeError:
            offer = ""
        if not offer.startswith("v="):
            raise HTTPError(400, "That isn't a WebRTC offer.")
        body = {"session": self.session_config(found["model"], found["voice"]),
                "transport": {"type": "webrtc", "sdp": offer}}
        request = urllib.request.Request(LIVE_SESSIONS, data=json.dumps(body).encode(), method="POST", headers={
            "Authorization": f"Bearer {found['key']}", "Content-Type": "application/json", "User-Agent": "Alfred"})
        try:
            status, answer = validators.fetch(request, timeout=SESSION_TIMEOUT)
        except validators.Offline:
            raise HTTPError(502, "Can't reach OpenAI: check your internet connection, then try again.") from None
        if status not in (200, 201):
            raise HTTPError(502, live_error(status, answer, found))
        try:
            sdp = json.loads(answer)["transport"]["sdp"]
        except (ValueError, KeyError, TypeError):
            sdp = None
        if not isinstance(sdp, str) or not sdp.startswith("v="):
            raise HTTPError(502, "OpenAI's answer didn't look right. Try again in a minute.")
        return Response(200, sdp.encode(), {"Content-Type": "application/sdp", "Cache-Control": "no-store"})

    # --- delegations --------------------------------------------------------------
    def delegate(self, req: Request) -> Response:
        """Run what the user said on Alfred and wait for its reply (the server is threaded)."""
        body = req.json()
        delegation_id, text = body.get("delegation_id"), body.get("text")
        if delegation_id is not None and not isinstance(delegation_id, str):
            raise HTTPError(400, "Which delegation? Its id must be text.")
        if not isinstance(text, str) or not text.strip():
            raise HTTPError(400, "Alfred didn't hear a request. Please say it again.")
        try:
            result = self.service.run_and_wait(text.strip(), "voice", timeout=self.delegate_timeout)
        except NotReady as e:
            raise HTTPError(409, str(e) or "Alfred isn't ready yet. Try again in a few seconds.") from e
        except QueueFull as e:
            raise HTTPError(429, str(e) or "Alfred has too many commands waiting.") from e
        except ValueError as e:
            raise HTTPError(400, str(e)) from e
        reply = " ".join(r.strip() for r in result.replies if r.strip())
        if result.stopped:
            reply = "Alfred was stopped before it finished."
        elif result.timed_out:
            reply = f"{reply} Alfred is still working on it. Its answer will appear in the Alfred window.".strip()
        elif not reply:
            reply = "Done." if result.ok else "That didn't work. The Alfred window has the details."
        return Response.json({"delegation_id": delegation_id, "id": result.id, "reply": speakable(reply),
                              "ok": result.ok, "stopped": result.stopped, "timed_out": result.timed_out})


def speakable(text: str, limit: int = SPOKEN_MAX) -> str:
    """Alfred's reply on one line, short enough for the live model. A long one is cut after a sentence."""
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    room = text[:limit - len(MORE)]
    cut = max(room.rfind(". "), room.rfind("! "), room.rfind("? ")) + 1
    if cut < len(room) // 2:  # no sentence end late enough: cut between words
        cut = room.rfind(" ")
    return room[:cut if cut > 0 else len(room)].rstrip() + MORE


def live_error(status: int, body: bytes, found: dict) -> str:
    """What went wrong starting a session, in plain words. Never the key."""
    model = found["model"]
    if status == 401:
        return "OpenAI rejected the API key. Check the OpenAI key in Settings, under API keys."
    if status == 403:
        return (f"Your OpenAI account can't use {model} yet. OpenAI may need to verify your organization first "
                "(platform.openai.com, Settings, Organization).")
    if status == 404:
        return f"OpenAI doesn't know the model {model}. Check OPENAI_LIVE_MODEL in Alfred's settings."
    if status == 429:
        return "OpenAI is limiting requests, or the account is out of credits. Check billing on platform.openai.com."
    if status >= 500:
        return f"OpenAI had a problem (error {status}). Try again in a minute."
    detail = _openai_message(body, found["key"])
    return f"OpenAI couldn't start voice mode (error {status})" + (f": {detail}" if detail else ".")


def _openai_message(body: bytes, key: str) -> str:
    try:
        message = str(json.loads(body)["error"]["message"])
    except (ValueError, KeyError, TypeError):
        return ""
    if key:
        message = message.replace(key, "(key)")
    return re.sub(r"\bsk-[\w*-]+", "(key)", " ".join(message.split()))[:200]


def register(router, service, store, **options) -> VoiceMode:
    """Add voice mode's routes to the app's router (server.py). Returns the VoiceMode."""
    voice = VoiceMode(service, store, **options)
    voice.add_routes(router)
    return voice
