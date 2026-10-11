"""Voice mode in the Alfred window (jarvis/app/voice.py) over FakeService and in-memory secrets.

No microphone, browser or OpenAI needed: the session request goes to a fake urlopen, and delegations run
on the stand-in engine. The page's side (WebRTC, the data channel) is checked by hand on Windows.
"""

import http.client
import io
import json
import os
import re
import sys
import threading
import time
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import config, consent, engine  # noqa: E402
from jarvis.app import main as app_main  # noqa: E402
from jarvis.app import voice  # noqa: E402
from jarvis.app.server import CSP, UI_DIR, App  # noqa: E402
from jarvis.onboarding import validators  # noqa: E402
from jarvis.service import QueueFull  # noqa: E402
from jarvis.service.fake import FakeService  # noqa: E402
from jarvis.settings import MemoryBackend, Store  # noqa: E402

OPENAI_KEY = "sk-proj-voicetestkey0123456789abcdef"
OFFER = "v=0\r\no=- 4611 2 IN IP4 127.0.0.1\r\ns=-\r\nt=0 0\r\na=group:BUNDLE 0 1\r\n"
ANSWER = "v=0\r\no=- 99 2 IN IP4 0.0.0.0\r\ns=-\r\nt=0 0\r\n"
CLEARED = ("JARVIS_ACCEPT_TERMS", "JARVIS_SETTINGS_FILE", "JARVIS_IGNORE_USER_SETTINGS", "OPENAI_API_KEY",
           "OPENAI_LIVE_MODEL", "OPENAI_LIVE_VOICE")


@pytest.fixture
def env(tmp_path, monkeypatch):
    saved = dict(os.environ)
    os.environ["LOCALAPPDATA"] = str(tmp_path)
    for key in CLEARED:
        os.environ.pop(key, None)
    monkeypatch.setattr(config, "MVP_DIR", tmp_path / "mvp")  # never the developer's own windows-mvp\.env
    yield tmp_path
    os.environ.clear()
    os.environ.update(saved)


def _app(env, *, key=OPENAI_KEY, consented=True):
    if consented:
        consent.record("test")
    store = Store(env / "settings.env", MemoryBackend({"OPENAI_API_KEY": key} if key else {}))
    app = App(FakeService(store, delay=0.01, consent=None), store=store, keepalive=0.2).start()
    app.start_service()
    return app


@pytest.fixture
def app(env):
    app = _app(env)
    assert app.service.wait_ready(5)
    yield app
    app.service.shutdown()
    app.close()


class Client:
    """The window: signs in with the one-time link, then sends the key (X-Alfred) and, on changes, its Origin."""

    def __init__(self, app):
        self.app = app
        conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
        url = urlsplit(app.login_url())
        conn.request("GET", f"{url.path}?{url.query}")
        page = conn.getresponse().read()
        conn.close()
        self.key = re.search(rb'<meta name="alfred-key" content="([^"]+)">', page).group(1).decode()

    def send(self, method, path, body=b"", content_type="application/json", **headers):
        sent = {"X-Alfred": self.key}
        if method != "GET":
            sent.update({"Origin": self.app.origin, "Content-Type": content_type})
        sent.update({k.replace("_", "-"): v for k, v in headers.items()})
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=30)
        conn.request(method, path, body=body if method != "GET" else None,
                     headers={k: v for k, v in sent.items() if v is not None})
        resp = conn.getresponse()
        out = resp.status, resp.getheader("Content-Type") or "", resp.read()
        conn.close()
        return out

    def call(self, method, path, body=None, **headers):
        status, _, data = self.send(method, path, json.dumps(body if body is not None else {}).encode(), **headers)
        return status, json.loads(data) if data else None

    def offer(self, sdp=OFFER, content_type="application/sdp", **headers):
        status, kind, data = self.send("POST", "/api/voice/session", sdp.encode(), content_type, **headers)
        return status, kind, data.decode()


class _Answer:
    def __init__(self, status, body):
        self.status, self.body = status, body

    def read(self, n=-1):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _urlopen(answers, seen):
    """A stand-in for urllib.request.urlopen: each call takes the next (status, body) or exception."""

    def urlopen(request, timeout):
        seen.append(request)
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        status, body = answer
        if status >= 400:
            raise urllib.error.HTTPError(request.full_url, status, "error", {}, io.BytesIO(body))
        return _Answer(status, body)

    return urlopen


def _created(sdp=ANSWER) -> tuple:
    return 200, json.dumps({"session": {"id": "sess_123"}, "transport": {"type": "webrtc", "sdp": sdp}}).encode()


# --- starting a session ------------------------------------------------------------------------------------------
def test_session_sends_the_offer_to_openai_live_and_returns_the_answer(app, monkeypatch, capsys):
    seen, answers = [], [_created(), _created()]
    monkeypatch.setattr(validators, "urlopen", _urlopen(answers, seen))
    client = Client(app)
    status, body = client.call("GET", "/api/voice/status")
    assert status == 200 and body == {"available": True, "reason": "", "message": "", "model": "gpt-live-1",
                                      "voice": "marin"}

    status, kind, sdp = client.offer()
    assert status == 200 and kind == "application/sdp" and sdp == ANSWER
    request = seen[0]
    assert request.full_url == "https://api.openai.com/v1/live/sessions" and request.get_method() == "POST"
    assert request.get_header("Authorization") == f"Bearer {OPENAI_KEY}"
    assert request.get_header("Content-type") == "application/json"
    sent = json.loads(request.data)
    assert sent["transport"] == {"type": "webrtc", "sdp": OFFER}
    session = sent["session"]
    assert session["model"] == "gpt-live-1" and session["audio"] == {"output": {"voice": "marin"}}
    assert session["delegation"] == {"type": "client"}  # tasks come to Alfred; no second brain at OpenAI
    assert session["store"] is False
    assert session["client"] == {"data_channel": {
        "allowed_client_events": ["session.thinking.append", "session.commentary.append", "session.close"],
        "allowed_server_events": "all"}}
    instructions = session["instructions"]
    assert "Alfred" in instructions and "Delegate" in instructions and "on screen" in instructions
    assert len(instructions) < 4000  # far below the 16k-token limit; short is better for a voice model
    assert "responses" not in json.dumps(session) and "web_search" not in json.dumps(session)

    # The voice and model are settings; changing them restarts nothing.
    changes = {"OPENAI_LIVE_VOICE": "cedar", "OPENAI_LIVE_MODEL": "gpt-live-2"}
    status, body = client.call("PUT", "/api/settings", changes)
    assert status == 200 and body == {"saved": ["OPENAI_LIVE_MODEL", "OPENAI_LIVE_VOICE"], "restarting": False}
    assert client.offer()[0] == 200
    assert json.loads(seen[1].data)["session"]["audio"]["output"]["voice"] == "cedar"
    assert json.loads(seen[1].data)["session"]["model"] == "gpt-live-2"
    assert client.call("PUT", "/api/settings", {"OPENAI_LIVE_VOICE": "Not a voice!"})[0] == 400
    assert client.call("GET", "/api/settings")[1]["defaults"]["OPENAI_LIVE_VOICE"] == "marin"

    # The key went only to OpenAI: never to the page, never to the console.
    _, _, raw = client.send("GET", "/api/voice/status")
    assert OPENAI_KEY.encode() not in raw
    out = capsys.readouterr()
    assert OPENAI_KEY not in out.out + out.err


def test_session_refuses_what_isnt_an_offer(app, monkeypatch):
    seen = []
    monkeypatch.setattr(validators, "urlopen", _urlopen([], seen))
    client = Client(app)
    assert client.offer(content_type="application/json")[0] == 415
    assert client.offer("hello")[0] == 400
    status, _, body = client.send("POST", "/api/voice/session", b"\xff\xfe", "application/sdp")
    assert status == 400
    assert seen == []


def test_openai_errors_become_plain_words_without_the_key(app, monkeypatch, capsys):
    seen = []
    leaky = json.dumps({"error": {"message": f"Invalid value for voice. Key used: {OPENAI_KEY} (sk-proj-****cdef)"}})
    answers = [(401, b"{}"), (403, b"{}"), (404, b"{}"), (429, b"{}"), (503, b""), (400, leaky.encode()),
               urllib.error.URLError("no route"), TimeoutError(), (200, b"not json"), _created("not sdp")]
    monkeypatch.setattr(validators, "urlopen", _urlopen(answers, seen))
    client = Client(app)
    results = [client.offer() for _ in range(10)]
    assert {status for status, _, _ in results} == {502}  # never 401: that would sign the window out
    messages = [json.loads(body)["error"] for _, _, body in results]
    assert "rejected the API key" in messages[0]
    assert "can't use gpt-live-1" in messages[1] and "verify your organization" in messages[1]
    assert "doesn't know the model gpt-live-1" in messages[2]
    assert "limiting requests" in messages[3] and "credits" in messages[3]
    assert "error 503" in messages[4]
    assert "error 400" in messages[5] and "Invalid value for voice" in messages[5]
    assert "Can't reach OpenAI" in messages[6] and "Can't reach OpenAI" in messages[7]
    assert "didn't look right" in messages[8] and "didn't look right" in messages[9]
    everything = json.dumps(messages) + "".join(capsys.readouterr())
    assert OPENAI_KEY not in everything and "sk-proj" not in everything and "cdef" not in everything
    assert len(seen) == 10


def test_voice_needs_consent_and_an_openai_key(env, monkeypatch):
    seen = []
    monkeypatch.setattr(validators, "urlopen", _urlopen([], seen))
    app = _app(env, key=None)
    try:
        client = Client(app)
        status, body = client.call("GET", "/api/voice/status")
        assert status == 200 and body["available"] is False and body["reason"] == "key"
        assert "OpenAI API key" in body["message"] and "Settings" in body["message"]
        status, _, data = client.offer()
        assert status == 409 and json.loads(data)["error"] == body["message"]

        # From source, a key in the environment (or windows-mvp\.env) works too, as for every setting.
        os.environ["OPENAI_API_KEY"] = OPENAI_KEY
        assert client.call("GET", "/api/voice/status")[1]["available"] is True
        del os.environ["OPENAI_API_KEY"]

        app.store.set_secret("OPENAI_API_KEY", OPENAI_KEY)
        assert client.call("GET", "/api/voice/status")[1]["available"] is True
        (config.data_dir() / "consent.json").unlink()  # the notice changed and wasn't accepted again
        status, body = client.call("GET", "/api/voice/status")
        assert body["available"] is False and body["reason"] == "consent" and "data notice" in body["message"]
        status, _, data = client.offer()
        assert status == 409 and "data notice" in json.loads(data)["error"]
        assert seen == []  # nothing went to OpenAI
    finally:
        app.service.shutdown()
        app.close()


def test_voice_routes_need_the_window_key_host_and_origin(app, monkeypatch):
    seen = []
    monkeypatch.setattr(validators, "urlopen", _urlopen([_created()], seen))
    client = Client(app)
    assert client.send("GET", "/api/voice/status", X_Alfred=None)[0] == 401
    assert client.offer(X_Alfred=None)[0] == 401
    assert client.offer(X_Alfred="1")[0] == 401
    assert client.offer(Origin="http://evil.example")[0] == 403
    assert client.offer(Origin=None)[0] == 403
    assert client.offer(Host=f"evil.example:{app.port}")[0] == 403
    assert client.call("POST", "/api/voice/delegate", {"text": "hi"}, Origin="http://evil.example")[0] == 403
    assert client.call("POST", "/api/voice/delegate", {"text": "hi"}, X_Alfred=None)[0] == 401
    assert client.send("GET", "/api/voice/session")[0] == 405
    assert seen == []
    assert client.offer()[0] == 200  # and with everything right, it works


# --- delegations ---------------------------------------------------------------------------------------------------
def test_delegate_runs_alfred_with_source_voice(app):
    client = Client(app)
    after = app.service.bus.last_seq
    status, body = client.call("POST", "/api/voice/delegate", {"delegation_id": "del_1", "text": "  what time is it  "})
    assert status == 200 and body["reply"] == "(fake) You said: what time is it"
    assert body["delegation_id"] == "del_1" and body["ok"] is True and not body["stopped"] and not body["timed_out"]
    # It shows in the window's log like a typed command, labelled as voice.
    events = app.service.events(after, timeout=1)
    command = next(e for e in events if e["type"] == "command")
    assert command["source"] == "voice" and command["text"] == "what time is it" and command["id"] == body["id"]
    assert next(e for e in events if e["type"] == "reply")["source"] == "voice"

    for bad in ({"text": ""}, {"text": "   "}, {"text": 5}, {}, {"text": "hi", "delegation_id": 7}):
        status, answer = client.call("POST", "/api/voice/delegate", bad)
        assert status == 400 and answer["error"], bad
    assert "say it again" in client.call("POST", "/api/voice/delegate", {"text": " "})[1]["error"]

    # A long reply is cut to what the live model takes; the window shows all of it.
    long = "Here is the news. " * 200
    status, body = client.call("POST", "/api/voice/delegate", {"delegation_id": None, "text": long})
    assert status == 200 and len(body["reply"]) <= voice.SPOKEN_MAX and body["reply"].endswith(voice.MORE)


def test_delegate_when_alfred_is_stopped_busy_or_not_ready(app):
    client = Client(app)
    answers = []
    worker = threading.Thread(target=lambda: answers.append(
        client.call("POST", "/api/voice/delegate", {"delegation_id": "d", "text": "long task"})))
    worker.start()
    deadline = time.monotonic() + 5
    while app.service.status().engine != "busy" and time.monotonic() < deadline:
        time.sleep(0.02)
    assert client.call("POST", "/api/stop")[1] == {"stopped": True}
    worker.join(10)
    status, body = answers[0]
    assert status == 200 and body["stopped"] is True and body["reply"] == "Alfred was stopped before it finished."
    assert app.service.wait_ready(5)

    app.voice.delegate_timeout = 0.2  # a long task: the voice says it's still going; the window gets the answer
    status, body = client.call("POST", "/api/voice/delegate", {"text": "long job"})
    assert status == 200 and body["timed_out"] is True and "still working" in body["reply"]
    client.call("POST", "/api/stop")
    assert app.service.wait_ready(5)

    def refuse(error):
        def run_and_wait(text, source="desktop", timeout=120):
            raise error
        return run_and_wait

    real = app.service.run_and_wait
    app.service.run_and_wait = refuse(QueueFull("The command queue is full. Wait for a reply before sending more."))
    status, body = client.call("POST", "/api/voice/delegate", {"text": "hello"})
    assert status == 429 and "queue is full" in body["error"]
    app.service.run_and_wait = refuse(ValueError("That command is too long."))
    status, body = client.call("POST", "/api/voice/delegate", {"text": "hello"})
    assert status == 400 and body == {"error": "That command is too long."}
    app.service.run_and_wait = real

    app.service.shutdown()  # Alfred's engine isn't running: a plain message for the voice to say
    status, body = client.call("POST", "/api/voice/delegate", {"text": "hello"})
    assert status == 409 and "Alfred" in body["error"]


def test_speakable():
    assert voice.speakable("  Done.\n\nOpened   Notepad. ") == "Done. Opened Notepad."
    text = "First sentence here. " + "word " * 400
    cut = voice.speakable(text, limit=200)
    assert len(cut) <= 200 and cut.endswith(voice.MORE) and " wor" not in cut[-len(voice.MORE) - 3:]
    sentences = "One two three. " * 40
    assert voice.speakable(sentences, limit=100) == "One two three. " * 3 + "One two three." + voice.MORE
    assert voice.speakable("x" * 300, limit=100) == "x" * (100 - len(voice.MORE)) + voice.MORE


def test_replies_to_voice_mode_are_not_spoken_by_windows():
    """The live voice says Alfred's reply; Windows' voice saying it too would talk over it, and the
    microphone would hear it as the user."""
    said = []
    worker = engine.Engine(io.StringIO())
    worker.speaker = SimpleNamespace(enabled=True, say=said.append)
    worker.current = {"id": "a", "source": "voice"}
    worker.reply("Opened Notepad.")
    worker.current = {"id": "b", "source": "desktop"}
    worker.reply("Typed it.")
    worker.announce("Reminder: tea.", "voice")  # a reminder set by voice, later, maybe after voice mode ended
    assert said == ["Typed it.", "Reminder: tea."]
    assert '"source": "voice", "text": "Opened Notepad."' in worker.out.getvalue()


# --- the window -------------------------------------------------------------------------------------------------------
def test_closing_the_window_to_the_tray_ends_voice_mode(app, monkeypatch):
    monkeypatch.setattr(app_main, "_session_ending", lambda: False)
    shell = app_main.Shell(app, app.service)
    shell.tray = SimpleNamespace(visible=True, notify=lambda text, title: None)
    shell.window = SimpleNamespace(hide=lambda: None)
    after = app.service.bus.last_seq
    assert shell._on_closing() is False  # hidden in the tray, still running
    assert [e for e in app.service.events(after, timeout=1) if e["type"] == "voice_off"][0]["reason"] == "closed"


def test_page_has_the_voice_button_and_keeps_the_csp(app):
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    button = re.search(r'<button[^>]*id="talk"[^>]*>', html).group(0)
    assert 'aria-pressed="false"' in button and 'aria-label="Voice mode"' in button and 'type="button"' in button
    assert '<audio id="voice-audio" autoplay>' in html and 'name="OPENAI_LIVE_VOICE"' in html
    assert html.index('id="talk"') < html.index('id="send"')  # next to Send, in the composer
    js = (UI_DIR / "app.js").read_text(encoding="utf-8")
    for needed in ('"/api/voice/session"', '"/api/voice/delegate"', '"/api/voice/status"', '"oai-events"',
                   '"session.close"', '"session.delegation.created"', '"session.input_transcript.delta"',
                   "getUserMedia", "srcObject", 'setAttribute("aria-pressed"'):
        assert needed in js, needed
    assert "api.openai.com" not in js  # the page never calls OpenAI's API itself
    names = re.findall(r"\bfunction (\w+)", js)
    assert len(names) == len(set(names))  # one script scope: a second "send" would quietly replace the first
    # WebRTC and the microphone need nothing from the CSP: the offer goes through Alfred's own server.
    assert "connect-src 'self'" in CSP and "openai" not in CSP
    status, _, _ = Client(app).send("GET", "/app.js")
    assert status == 200
