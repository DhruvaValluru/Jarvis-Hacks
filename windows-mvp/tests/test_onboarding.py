"""The first-run setup (jarvis/onboarding) over FakeService and in-memory secrets.

No Windows, network, microphone or Hugging Face needed: the key checks get a fake urlopen and a fake
Anthropic client, the speech download runs a stand-in process, and the startup list is a dict.
"""

import http.client
import io
import json
import os
import re
import sys
import time
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import config, consent, onboarding, remote, voice  # noqa: E402
from jarvis.app.http import HTTPError, Request, Router  # noqa: E402
from jarvis.app.server import CSP, UI_DIR, App  # noqa: E402
from jarvis.onboarding import speech, system, validators  # noqa: E402
from jarvis.onboarding.state import STEPS, SetupState  # noqa: E402
from jarvis.service.bridge import TelegramController  # noqa: E402
from jarvis.service.events import EventBus  # noqa: E402
from jarvis.service.fake import FakeService  # noqa: E402
from jarvis.settings import MemoryBackend, Store  # noqa: E402

ANTHROPIC_KEY = "sk-ant-api03-abcdefghijklmnopqrstuvwxyz-9f2c"
OPENAI_KEY = "sk-proj-abcdefghijklmnopqrstuvwxyz0123"
BOT_TOKEN = "123456789:AAE_abcdefghijklmnopqrstuvwxyz012345"
CLEARED = ("JARVIS_ACCEPT_TERMS", "JARVIS_SETTINGS_FILE", "JARVIS_IGNORE_USER_SETTINGS", "ANTHROPIC_API_KEY",
           "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "SPECTRUM_PROJECT_SECRET",
           "TELEGRAM_OWNER_ID", "JARVIS_TOKEN", "JARVIS_WHISPER_MODEL", "JARVIS_MIC")


@pytest.fixture
def env(tmp_path):
    saved = dict(os.environ)
    os.environ["LOCALAPPDATA"] = str(tmp_path)
    for key in CLEARED:
        os.environ.pop(key, None)
    yield tmp_path
    os.environ.clear()
    os.environ.update(saved)


def call(router, method, path, body=None):
    """Call a route the way the window does (signing in is the app's job: see test_routes_need_sign_in)."""
    found = router.match(method, path)
    assert found is not None, f"no route {method} {path}"
    handler, params, _ = found
    req = Request(method, path, body=b"" if body is None else json.dumps(body).encode())
    try:
        resp = handler(req, **params)
    except HTTPError as e:
        return e.status, {"error": e.message}
    return resp.status, json.loads(resp.body) if resp.body else None


class Setup:
    def __init__(self, env, service=None, **options):
        self.store = Store(env / "settings.env", MemoryBackend())
        self.service = service or FakeService(self.store, delay=0.01, consent=None)
        self.router = Router()
        self.wizard = onboarding.register(self.router, self.service, self.store, **options)
        self.wizard.legacy_files = []  # never the developer's own windows-mvp\.env

    def call(self, method, path, body=None):
        return call(self.router, method, "/api/onboarding/" + path, body)

    def state(self) -> dict:
        return self.call("GET", "state")[1]

    def step(self, name, status="done"):
        return self.call("POST", "step", {"step": name, "status": status})


def _status(state, name):
    return next(s for s in state["steps"] if s["name"] == name)


# --- steps -----------------------------------------------------------------------------------
def test_steps_in_order_skip_and_resume(env):
    setup = Setup(env)
    state = setup.state()
    assert [s["name"] for s in state["steps"]] == list(STEPS) == [
        "welcome", "consent", "import", "anthropic", "safety", "speech", "openai", "telegram", "pcs", "finish"]
    assert state["current"] == "welcome" and state["complete"] is False
    assert not setup.wizard.state.exists()  # nothing written until a step is answered

    assert setup.step("welcome", "skipped")[0] == 409  # required
    assert setup.step("welcome")[0] == 200
    assert setup.state()["current"] == "consent"
    assert setup.call("POST", "consent", {"version": consent.TERMS_VERSION})[0] == 200
    state = setup.state()
    assert _status(state, "import")["available"] is False  # no old .env: the step is left out
    assert state["current"] == "anthropic"

    assert setup.step("anthropic")[0] == 409  # no key yet
    assert setup.step("anthropic", "skipped")[0] == 200
    assert setup.step("safety", "skipped")[0] == 409
    assert setup.step("safety")[0] == 200
    assert setup.state()["current"] == "speech"
    assert setup.step("nope")[0] == 404
    assert setup.step("speech", "maybe")[0] == 400

    # Alfred restarts here: the setup opens again where it stopped.
    again = Setup(env)
    assert again.state()["current"] == "speech" and again.state()["complete"] is False
    for name in ("speech", "openai", "telegram", "pcs"):
        assert again.step(name, "skipped")[0] == 200
    assert again.state()["current"] == "finish"
    status, state = again.step("finish")
    assert status == 200 and state["complete"] is True and re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d", state["completed_at"])
    saved = json.loads((env / "Jarvis" / "onboarding.json").read_text(encoding="utf-8"))
    assert saved["version"] == 1 and saved["completed_at"] == state["completed_at"]
    assert saved["steps"] == {"welcome": "done", "consent": "done", "anthropic": "skipped", "safety": "done",
                              "speech": "skipped", "openai": "skipped", "telegram": "skipped", "pcs": "skipped",
                              "finish": "done"}
    # Revisiting a step later (from Settings) keeps the setup complete.
    assert again.step("anthropic", "skipped")[0] == 200 and again.state()["complete"] is True


def test_state_file_round_trip(tmp_path):
    path = tmp_path / "onboarding.json"
    state = SetupState(path)
    assert not state.exists()
    assert state.read() == {"version": 1, "steps": {}, "completed_at": None}
    state.mark("welcome", "done")
    state.mark("anthropic", "skipped")
    expected = {"version": 1, "steps": {"welcome": "done", "anthropic": "skipped"}, "completed_at": None}
    assert json.loads(path.read_text(encoding="utf-8")) == expected
    assert SetupState(path).read() == expected
    assert SetupState(path).mark("finish", "done")["completed_at"]
    with pytest.raises(ValueError):
        state.mark("nope", "done")
    with pytest.raises(ValueError):
        state.mark("welcome", "maybe")
    assert not list(tmp_path.glob("*.tmp"))

    path.write_text('{"steps": {"welcome": "done", "bogus": "done", "consent": "yes"}, "completed_at": 5}', encoding="utf-8")
    assert SetupState(path).read() == {"version": 1, "steps": {"welcome": "done"}, "completed_at": None}
    for junk in ("not json", "[]", '{"steps": []}'):
        path.write_text(junk, encoding="utf-8")
        assert SetupState(path).read() == {"version": 1, "steps": {}, "completed_at": None}


def test_consent_gate(env):
    setup = Setup(env)
    assert setup.service.start().engine == "needs_consent"
    assert setup.step("welcome")[0] == 200
    assert setup.step("consent")[0] == 409  # only "I agree" accepts the notice
    assert setup.step("consent", "skipped")[0] == 409
    assert setup.step("finish")[0] == 409

    status, notice = setup.call("GET", "consent")
    assert status == 200 and notice["accepted"] is False and "Anthropic" in notice["notice_markdown"]
    assert setup.call("POST", "consent", {"version": "1999-01-01"})[0] == 409  # the notice changed meanwhile
    assert not consent.accepted()

    status, body = setup.call("POST", "consent", {"version": notice["version"]})
    assert status == 200 and body["accepted"] is True
    assert consent.accepted()
    assert json.loads((env / "Jarvis" / "consent.json").read_text(encoding="utf-8"))["source"] == "app"
    assert setup.service.wait_ready(5)  # agreeing starts Alfred
    assert _status(body["state"], "consent")["status"] == "done"
    status, body = setup.step("finish")
    assert status == 409 and "Safety" in body["error"]


def test_welcome_blocks_only_an_unsupported_windows(env, monkeypatch):
    store = Store(env / "settings.env", MemoryBackend())
    router = Router()
    onboarding.register(router, SimpleNamespace(), store)  # a real engine, not the stand-in
    old = {"windows": True, "supported": False, "text": "An older Windows (build 9600)."}
    monkeypatch.setattr(system, "windows", lambda: old)
    monkeypatch.setattr(system, "has_office", lambda: False)
    monkeypatch.setattr(system, "has_tailscale", lambda: (_ for _ in ()).throw(OSError("no")))
    report = system.check()
    assert report["blocking"] is True and report["supported"] is False
    assert [c["name"] for c in report["checks"]] == ["windows", "microphone", "office", "tailscale"]
    assert "Windows 10" in report["checks"][0]["detail"]
    office, tailscale = report["checks"][2], report["checks"][3]
    assert office["ok"] is False and "everything else works" in office["detail"]
    assert tailscale["ok"] is None and "Couldn't check" in tailscale["detail"]  # information, never an error
    assert system.check(any_os=True)["blocking"] is False
    assert call(router, "POST", "/api/onboarding/step", {"step": "welcome", "status": "done"})[0] == 409

    monkeypatch.setattr(system, "windows", lambda: {**old, "supported": True, "text": "Windows 11 Pro (build 26100)"})
    assert system.check()["blocking"] is False
    assert call(router, "POST", "/api/onboarding/step", {"step": "welcome", "status": "done"})[0] == 200


# --- keys --------------------------------------------------------------------------------------
def _client(error=None, seen=None):
    """A stand-in for anthropic.Anthropic(api_key=...): models.list(limit=1) raises error or answers."""

    def factory(key):
        def models_list(limit):
            assert limit == 1
            if seen is not None:
                seen.append(key)
            if error is not None:
                raise error
            return []

        return SimpleNamespace(models=SimpleNamespace(list=models_list))

    return factory


def test_anthropic_errors_are_mapped(env):
    anthropic = pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx2")
    request = httpx.Request("GET", "https://api.anthropic.com/v1/models")

    def response(code):
        return httpx.Response(code, request=request)

    cases = [
        (anthropic.AuthenticationError("bad key", response=response(401), body=None), "rejected", "Key rejected"),
        (anthropic.PermissionDeniedError("no", response=response(403), body=None), "forbidden", "lacks permission"),
        (anthropic.APIConnectionError(request=request), "offline", "Can't reach Anthropic"),
        (anthropic.APITimeoutError(request=request), "offline", "check your internet"),
        (anthropic.InternalServerError("oops", response=response(500), body=None), "error", "error 500"),
        (ValueError("odd"), "error", "ValueError"),
    ]
    for error, result, words in cases:
        check = validators.check_anthropic(ANTHROPIC_KEY, _client(error))
        assert (check.ok, check.result) == (False, result), error
        assert words in check.message and ANTHROPIC_KEY not in check.message
    assert validators.check_anthropic("  ", _client()).result == "empty"
    assert validators.check_anthropic("sk ant with spaces in it ok", _client()).result == "format"

    setup = Setup(env, anthropic_client=_client(cases[0][0]))
    status, body = setup.call("POST", "anthropic", {"key": ANTHROPIC_KEY})
    assert status == 422 and body["result"] == "rejected" and "Key rejected" in body["error"]
    assert setup.call("POST", "anthropic", {"key": ANTHROPIC_KEY, "save_anyway": True})[0] == 422  # never a rejected key
    assert not setup.store.has_secret("ANTHROPIC_API_KEY")

    setup.wizard.anthropic_client = _client(cases[2][0])  # offline: may be saved without checking
    status, body = setup.call("POST", "anthropic", {"key": ANTHROPIC_KEY, "save_anyway": True})
    assert status == 200 and body["saved"] is True and setup.store.has_secret("ANTHROPIC_API_KEY")

    seen = []
    setup.wizard.anthropic_client = _client(seen=seen)
    status, body = setup.call("POST", "anthropic", {"key": f"  {ANTHROPIC_KEY}\n"})
    assert status == 200 and body["ok"] is True and body["hint"] == "sk-ant-...9f2c"
    assert seen == [ANTHROPIC_KEY]  # checked as pasted, minus the spaces
    assert ANTHROPIC_KEY not in json.dumps(body)  # the key never comes back
    assert setup.store.get_secret("ANTHROPIC_API_KEY") == ANTHROPIC_KEY
    assert _status(setup.state(), "anthropic")["status"] == "done"


class _Answer:
    def __init__(self, status, body):
        self.status, self._body = status, body

    def read(self, n=-1):
        return self._body

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


def test_openai_and_telegram_checks_with_a_fake_urlopen(env, monkeypatch):
    seen = []
    answers = [(200, b'{"data": []}'), (401, b"{}"), (403, b"{}"), urllib.error.URLError("no route"), (500, b"")]
    monkeypatch.setattr(validators, "urlopen", _urlopen(answers, seen))
    results = [validators.check_openai(OPENAI_KEY) for _ in range(5)]
    assert [(c.ok, c.result) for c in results] == [
        (True, "ok"), (False, "rejected"), (False, "forbidden"), (False, "offline"), (False, "error")]
    assert "Key rejected" in results[1].message and "lacks permission" in results[2].message
    assert "Can't reach OpenAI" in results[3].message
    assert seen[0].full_url == "https://api.openai.com/v1/models" and seen[0].get_method() == "GET"
    assert seen[0].get_header("Authorization") == f"Bearer {OPENAI_KEY}"
    assert all(OPENAI_KEY not in c.message for c in results)
    assert validators.check_openai("").result == "empty" and len(seen) == 5

    seen.clear()
    me = {"ok": True, "result": {"id": 123456789, "is_bot": True, "first_name": "Alfred", "username": "alfred_home_bot"}}
    answers[:] = [(200, json.dumps(me).encode()), (401, b"{}"), (404, b"{}"), TimeoutError(),
                  (200, b"not json"), (200, json.dumps({**me, "result": {**me["result"], "username": "<b>x</b>"}}).encode())]
    assert validators.check_telegram_token("not a token").result == "format" and not seen  # no network for that
    check = validators.check_telegram_token(f" {BOT_TOKEN} ")
    assert check.ok and check.info == {"username": "alfred_home_bot", "name": "Alfred", "link": "https://t.me/alfred_home_bot"}
    assert seen[0].full_url == f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
    results = [validators.check_telegram_token(BOT_TOKEN) for _ in range(5)]
    assert [c.result for c in results] == ["rejected", "rejected", "offline", "error", "error"]
    assert all(BOT_TOKEN not in c.message and "123456789" not in c.message for c in [check, *results])

    # Through the routes: saved only when the check passes; the bot's name and link are shown.
    setup = Setup(env)
    answers[:] = [(401, b"{}"), (200, json.dumps(me).encode()), (200, b"{}")]
    assert setup.call("POST", "telegram/token", {"token": BOT_TOKEN})[0] == 422
    assert not setup.store.has_secret("TELEGRAM_BOT_TOKEN")
    status, body = setup.call("POST", "telegram/token", {"token": BOT_TOKEN})
    assert status == 200 and body["link"] == "https://t.me/alfred_home_bot" and BOT_TOKEN not in json.dumps(body)
    assert setup.store.get_secret("TELEGRAM_BOT_TOKEN") == BOT_TOKEN
    assert setup.call("GET", "settings")[1]["bot"]["username"] == "alfred_home_bot"
    assert setup.call("POST", "telegram/spectrum", {"secret": ""})[0] == 422
    assert setup.call("POST", "telegram/spectrum", {"secret": "has spaces in it"})[0] == 422
    assert setup.call("POST", "telegram/spectrum", {"secret": "spectrum-secret-1234"})[0] == 200
    status, body = setup.call("POST", "openai", {"key": OPENAI_KEY})
    assert status == 200 and setup.store.get_secret("OPENAI_API_KEY") == OPENAI_KEY
    assert _status(setup.state(), "openai")["status"] == "done"


# --- Telegram owner --------------------------------------------------------------------------------
def test_telegram_owner_only_with_the_exact_code(env):
    clock = [1000.0]
    setup = Setup(env)
    setup.wizard.link.clock = lambda: clock[0]
    assert setup.call("POST", "telegram/link")[0] == 409  # bot token and Spectrum secret first
    setup.store.set_secret("TELEGRAM_BOT_TOKEN", BOT_TOKEN)
    setup.store.set_secret("SPECTRUM_PROJECT_SECRET", "spectrum-secret-1234")
    setup.store.set("TELEGRAM_OWNER_ID", "555")  # linked before

    status, link = setup.call("POST", "telegram/link")
    code = link["code"]
    assert status == 200 and link["state"] == "waiting" and re.fullmatch(r"[0-9]{6}", code)
    assert link["expires_in"] == 600
    assert setup.store.get("TELEGRAM_OWNER_ID") is None  # discovery mode while linking: nobody can run anything
    assert setup.store.get("JARVIS_TELEGRAM") == "1" and setup.service.status().telegram == "running"

    def message(user, text):
        setup.service.bus.publish("telegram_user", id=user, text=text)

    wrong = "000000" if code != "000000" else "111111"
    message("777", "/start")
    message("777", f"my code is {code}")
    message("777", code + "0")
    message("not-an-id", code)
    message("", code)
    message("888", wrong)  # a wrong guess counts
    status, link = setup.call("GET", "telegram/link")
    assert link["state"] == "waiting" and link["wrong"] == 1 and link["owner"] == ""
    assert setup.store.get("TELEGRAM_OWNER_ID") is None

    message("4242", f"  {code[:3]} {code[3:]} ")  # typed as shown, with the space
    status, link = setup.call("GET", "telegram/link")
    assert link["state"] == "linked" and link["owner"] == "4242" and link["code"] == ""
    assert setup.store.get("TELEGRAM_OWNER_ID") == "4242"
    message("777", code)  # too late, and the code worked once
    assert setup.store.get("TELEGRAM_OWNER_ID") == "4242"
    assert _status(setup.state(), "telegram")["status"] == "done"

    # Five wrong codes end the attempt; the right one then does nothing and the owner stays.
    code = setup.call("POST", "telegram/link")[1]["code"]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        message("999", wrong)
    message("999", code)
    link = setup.call("GET", "telegram/link")[1]
    assert link["state"] == "locked" and link["code"] == ""
    assert setup.store.get("TELEGRAM_OWNER_ID") == "4242"  # put back

    # A code expires after 10 minutes.
    code = setup.call("POST", "telegram/link")[1]["code"]
    clock[0] += 601
    message("31337", code)
    assert setup.call("GET", "telegram/link")[1]["state"] == "expired"
    assert setup.store.get("TELEGRAM_OWNER_ID") == "4242"

    # Cancelling puts the owner back too.
    setup.call("POST", "telegram/link")
    assert setup.store.get("TELEGRAM_OWNER_ID") is None
    assert setup.call("DELETE", "telegram/link")[1]["state"] == "cancelled"
    assert setup.store.get("TELEGRAM_OWNER_ID") == "4242"


def test_bridge_reports_what_discovery_users_typed():
    service = SimpleNamespace(bus=EventBus(), store=None)
    bridge = TelegramController(service)
    bridge._on_bridge({"type": "telegram_user", "id": 42, "text": "123456"})
    bridge._on_bridge({"type": "telegram_user", "id": 43, "text": "y" * 100})
    bridge._on_bridge({"type": "telegram_user", "id": 44})
    events = service.bus.events(0, timeout=0)
    assert [(e["type"], e["id"], e["text"]) for e in events] == [
        ("telegram_user", "42", "123456"), ("telegram_user", "43", "y" * 32), ("telegram_user", "44", "")]


# --- other PCs ---------------------------------------------------------------------------------------
def test_pairing_codes(env):
    code = validators.new_pairing_code()
    assert len(code) >= 32 and re.fullmatch(r"[A-Za-z0-9_-]+", code) and validators.new_pairing_code() != code
    grouped = validators.group(code)
    assert grouped.replace(" ", "") == code and all(len(g) == 4 for g in grouped.split(" ")[:-1])
    for typed in (f"  {grouped}\n", f"JARVIS_TOKEN={code}", f'"{code}"', grouped.replace(" ", "\n")):
        assert validators.normalize_pairing_code(typed) == code
    assert validators.check_pairing_code(grouped).ok
    assert validators.check_pairing_code("").result == "empty"
    assert validators.check_pairing_code("too short").result == "format"
    assert validators.check_pairing_code("x" * 300).result == "format"
    assert validators.MIN_PAIRING == remote.MIN_TOKEN

    setup = Setup(env)
    status, made = setup.call("POST", "pcs/new")
    assert status == 200 and setup.store.get_secret("JARVIS_TOKEN") == made["pairing_code"]
    assert made["grouped"] == validators.group(made["pairing_code"])
    status, body = setup.call("POST", "pcs/new")  # a second code would cut off the other PCs: ask first
    assert status == 409 and body["result"] == "exists" and made["pairing_code"] not in json.dumps(body)
    status, again = setup.call("POST", "pcs/new", {"replace": True})
    assert status == 200 and again["pairing_code"] != made["pairing_code"]

    other = validators.new_pairing_code()
    status, body = setup.call("POST", "pcs/join", {"code": validators.group(other)})
    assert status == 200 and setup.store.get_secret("JARVIS_TOKEN") == other and other not in json.dumps(body)
    assert setup.call("POST", "pcs/join", {"code": "abc def"})[0] == 422
    assert setup.store.get_secret("JARVIS_TOKEN") == other

    status, body = setup.call("PUT", "pcs", {"name": "Desk PC", "serve": True})
    assert status == 200 and body["saved"] == ["JARVIS_NAME", "JARVIS_SERVE"]
    assert setup.store.values()["JARVIS_NAME"] == "Desk PC" and setup.store.values()["JARVIS_SERVE"] == "1"
    assert setup.call("PUT", "pcs", {"name": "bad/name!"})[0] == 422
    assert setup.call("PUT", "pcs", {"serve": "yes"})[0] == 400
    assert setup.call("PUT", "pcs", {"serve": False})[1]["saved"] == ["JARVIS_SERVE"]
    assert "JARVIS_SERVE" not in setup.store.values()  # off is the default
    assert setup.step("pcs")[0] == 200


# --- finish ----------------------------------------------------------------------------------------------
class FakeRunKey:
    def __init__(self):
        self.values = {}

    def get(self, name):
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value

    def delete(self, name):
        self.values.pop(name, None)


def test_autostart_is_off_until_turned_on(env, monkeypatch):
    key = FakeRunKey()
    setup = Setup(env, autostart=system.Autostart(key))
    status, auto = setup.call("GET", "autostart")
    assert status == 200 and auto["supported"] is True and auto["enabled"] is False and not key.values

    status, auto = setup.call("PUT", "autostart", {"on": True})
    command = system.launch_command()
    assert status == 200 and auto["enabled"] is True and key.values == {"Alfred": command}
    assert "jarvis.app" in command and "runpy" in command and str(config.MVP_DIR) in command
    assert setup.call("PUT", "autostart", {"on": "yes"})[0] == 400
    status, auto = setup.call("PUT", "autostart", {"on": False})
    assert auto["enabled"] is False and key.values == {}

    monkeypatch.setattr(config, "frozen", lambda: True)
    monkeypatch.setattr(sys, "executable", r"C:\Program Files\Alfred\Alfred.exe")
    assert system.launch_command() == r'"C:\Program Files\Alfred\Alfred.exe"'
    monkeypatch.setattr(sys, "platform", "linux")
    nowhere = Setup(env, autostart=system.Autostart())
    assert nowhere.call("GET", "autostart")[1] == {"supported": False, "enabled": False, "command": ""}
    assert nowhere.call("PUT", "autostart", {"on": True})[0] == 409


def test_safety_and_try_it(env):
    setup = Setup(env)
    setup.call("POST", "consent", {"version": consent.TERMS_VERSION})
    assert setup.service.wait_ready(5)
    assert setup.call("GET", "settings")[1]["computer_use"] is True  # on by default
    status, body = setup.call("POST", "safety", {"computer_use": False})
    assert status == 200 and body["restarting"] is True and setup.store.values()["JARVIS_COMPUTER_USE"] == "0"
    assert _status(body["state"], "safety")["status"] == "done"
    assert setup.call("POST", "safety", {"computer_use": "no"})[0] == 400
    assert setup.call("POST", "safety", {"computer_use": True})[1]["computer_use"] is True
    status, body = setup.call("POST", "try")
    assert status == 200 and body["ok"] is True and body["replies"] == ["(fake) You said: open notepad"]


# --- old settings ------------------------------------------------------------------------------------------
def test_legacy_import(env):
    old = env / "old" / ".env"
    old.parent.mkdir()
    text = ("# from the console version\nANTHROPIC_API_KEY=sk-ant-api03-OLDKEYabcdefghijklmnop-1234\n"
            "JARVIS_MODEL=claude-opus-5-5\nSOME_PASSWORD=hunter2hunter2\nlowercase=x\nJARVIS_EFFORT=\n")
    old.write_text(text, encoding="utf-8")
    setup = Setup(env)
    assert _status(setup.state(), "import")["available"] is False
    assert setup.call("POST", "legacy/import")[0] == 404
    setup.wizard.legacy_files = [env / "missing" / ".env", old]
    assert _status(setup.state(), "import")["available"] is True

    status, body = setup.call("GET", "legacy")
    assert status == 200 and body["found"] is True and body["path"] == str(old)
    assert body["keys"] == [
        {"name": "ANTHROPIC_API_KEY", "secret": True, "value": "sk-ant-...1234"},
        {"name": "JARVIS_MODEL", "secret": False, "value": "claude-opus-5-5"},
        {"name": "SOME_PASSWORD", "secret": True, "value": "(hidden)"},
    ]
    assert "OLDKEY" not in json.dumps(body) and "hunter2" not in json.dumps(body)

    status, body = setup.call("POST", "legacy/import")
    assert status == 200 and "ANTHROPIC_API_KEY" in body["imported"] and "JARVIS_MODEL" in body["imported"]
    assert setup.store.get_secret("ANTHROPIC_API_KEY") == "sk-ant-api03-OLDKEYabcdefghijklmnop-1234"
    assert setup.store.values()["JARVIS_MODEL"] == "claude-opus-5-5"
    assert "ANTHROPIC_API_KEY" not in setup.store.values()  # keys never land in settings.env
    assert old.read_text(encoding="utf-8") == text  # the old file stays as it is
    state = body["state"]
    assert _status(state, "import")["status"] == "done" and _status(state, "anthropic")["ready"] is True

    # A file the user picks: its text is sent, read from a private copy that is removed straight away.
    status, body = setup.call("POST", "legacy/preview", {"text": f"OPENAI_API_KEY={OPENAI_KEY}\n"})
    assert status == 200 and body["keys"] == [{"name": "OPENAI_API_KEY", "secret": True, "value": "sk-proj-...0123"}]
    status, body = setup.call("POST", "legacy/import", {"text": f"OPENAI_API_KEY={OPENAI_KEY}\n"})
    assert status == 200 and body["imported"] == ["OPENAI_API_KEY"]
    assert setup.store.get_secret("OPENAI_API_KEY") == OPENAI_KEY
    assert not list(config.data_dir().glob("import-*"))
    assert setup.call("POST", "legacy/preview", {"text": " "})[0] == 400
    assert setup.call("POST", "legacy/preview", {"text": "A=b\n" * 20000})[0] == 400
    assert setup.call("POST", "legacy/import", {"text": "# nothing here\n"})[0] == 400


# --- voice -----------------------------------------------------------------------------------------------------
def _fake_download(behaviour):
    """A stand-in for `python -m jarvis.onboarding.speech NAME FOLDER` that writes into the same folder."""
    code = (
        "import json, os, sys, time\n"
        "name, folder = sys.argv[1], sys.argv[2]\n"
        "repo = os.path.join(folder, 'models--Systran--faster-whisper-' + name)\n"
        "os.makedirs(os.path.join(repo, 'blobs'), exist_ok=True)\n"
        "print(json.dumps({'type': 'total', 'bytes': 1000}), flush=True)\n"
        "open(os.path.join(repo, 'blobs', 'abc.incomplete'), 'wb').write(b'x' * 400)\n"
    )
    if behaviour == "slow":
        code += "time.sleep(60)\n"
    elif behaviour == "fail":
        code += "print(json.dumps({'type': 'error', 'error': 'No internet.'}), flush=True)\nsys.exit(1)\n"
    else:
        code += ("snap = os.path.join(repo, 'snapshots', 'abc123')\nos.makedirs(snap)\n"
                 "open(os.path.join(snap, 'model.bin'), 'wb').write(b'm' * 600)\n"
                 "print(json.dumps({'type': 'done'}), flush=True)\n")
    return lambda name, folder: [sys.executable, "-c", code, name, folder]


def _wait(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False


def test_speech_model_download_progress_and_cancel(env):
    downloads = speech.ModelDownload(_fake_download("slow"))
    setup = Setup(env, downloads=downloads)
    status, progress = setup.call("POST", "speech/download")
    assert status == 200 and progress["state"] == "downloading" and progress["model"] == "base.en"
    assert _wait(lambda: setup.call("GET", "speech/download")[1]["bytes"] >= 400)
    progress = setup.call("GET", "speech/download")[1]
    assert progress["total"] == 1000 and progress["percent"] == 40
    assert setup.call("POST", "speech/download")[0] == 409  # one at a time
    status, progress = setup.call("DELETE", "speech/download")
    assert progress["state"] == "cancelled"
    assert downloads.wait(10)  # the download process really ended
    assert setup.call("GET", "speech/download")[1]["state"] == "cancelled"
    assert not speech.present("base.en") and voice.model_root("base.en") is None

    downloads.command = _fake_download("fail")
    setup.call("POST", "speech/download")
    assert downloads.wait(10)
    progress = setup.call("GET", "speech/download")[1]
    assert progress["state"] == "error" and progress["error"] == "No internet."

    downloads.command = _fake_download("done")
    setup.call("POST", "speech/download")
    assert downloads.wait(10)
    progress = setup.call("GET", "speech/download")[1]
    assert progress["state"] == "done" and progress["percent"] == 100
    assert speech.present("base.en") and voice.model_root("base.en") == str(config.models_dir())
    status, body = setup.call("GET", "speech")
    assert status == 200 and body["present"] is True and body["model"] == "base.en" and body["voice_test"] is False
    assert set(body["microphones"]) == {"available", "devices", "error"}


def test_microphone_choice_and_voice_test(env, monkeypatch):
    devices = [{"index": 0, "name": "Realtek Mic", "default": True}, {"index": 3, "name": "USB Mic", "default": False}]
    monkeypatch.setattr(voice, "input_devices", lambda: devices)
    setup = Setup(env)
    assert setup.call("GET", "speech")[1]["microphones"]["devices"] == [
        {"name": "Realtek Mic", "default": True}, {"name": "USB Mic", "default": False}]
    assert setup.call("PUT", "microphone", {"name": "USB Mic"})[0] == 200
    assert setup.store.get("JARVIS_MIC") == "USB Mic" and voice.input_device("USB Mic") == 3
    assert setup.call("PUT", "microphone", {"name": "Unplugged"})[0] == 400
    assert setup.call("PUT", "microphone", {"name": ""})[0] == 200 and setup.store.get("JARVIS_MIC") is None
    assert voice.input_device("") is None and voice.input_device("Unplugged") is None

    # The service has no voice test yet: the setup says so and moves on.
    status, body = setup.call("POST", "speech/test")
    assert status == 200 and body["supported"] is False and body["message"]

    class VoiceService(FakeService):
        def voice_test(self, mic=None):
            self.mic = mic
            return {"heard": "what time is it", "reply": "It's 10:30."}

    store = Store(env / "voice.env", MemoryBackend())
    store.set("JARVIS_MIC", "USB Mic")
    service = VoiceService(store, delay=0.01, consent=None)
    router = Router()
    onboarding.register(router, service, store)
    status, body = call(router, "POST", "/api/onboarding/speech/test")
    assert (status, body) == (200, {"supported": True, "heard": "what time is it", "reply": "It's 10:30."})
    assert service.mic == "USB Mic"


def test_listener_loads_the_downloaded_model_and_the_chosen_microphone(env, monkeypatch):
    np = pytest.importorskip("numpy")
    made = {}

    class FakeModel:
        def __init__(self, name, **options):
            made.update(name=name, **options)

    def rec(frames, **options):
        made["rec"] = options
        return np.zeros((frames, 1), dtype="float32")

    sounddevice = SimpleNamespace(
        default=SimpleNamespace(hostapi=0, device=(0, 1)), rec=rec, wait=lambda: None,
        query_devices=lambda: [
            {"name": "Realtek Mic", "hostapi": 0, "max_input_channels": 2},
            {"name": "Speakers", "hostapi": 0, "max_input_channels": 0},
            {"name": "USB Mic", "hostapi": 0, "max_input_channels": 1},
            {"name": "USB Mic", "hostapi": 1, "max_input_channels": 1},  # the same mic on another sound system
        ])
    monkeypatch.setitem(sys.modules, "sounddevice", sounddevice)
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=FakeModel))
    assert [d["name"] for d in voice.input_devices()] == ["Realtek Mic", "USB Mic"]

    voice.Listener("base.en", mic="USB Mic")
    assert made["name"] == "base.en" and made["download_root"] is None and made["rec"]["device"] == 2
    snapshot = voice.model_folder("base.en") / "snapshots" / "abc123"
    snapshot.mkdir(parents=True)
    (snapshot / "model.bin").write_bytes(b"m")
    voice.Listener("base.en", mic="")
    assert made["download_root"] == str(config.models_dir()) and made["rec"]["device"] is None
    assert "JARVIS_WHISPER_MODEL" not in os.environ  # the setting itself is untouched
    assert voice.model_repo("base.en") == "Systran/faster-whisper-base.en"
    assert voice.model_repo("distil-large-v3") == "Systran/faster-distil-whisper-large-v3"
    assert voice.model_repo("someone/custom") == "someone/custom"


# --- the app ------------------------------------------------------------------------------------------------------
def test_routes_need_sign_in_and_the_page_is_served(env):
    consent.record("test")
    store = Store(env / "settings.env", MemoryBackend())
    service = FakeService(store, delay=0.01, consent=None)
    app = App(service, store=store, keepalive=0.2).start()

    def raw(method, path, headers=None, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", app.port, timeout=10)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        out = resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
        conn.close()
        return out

    try:
        assert raw("GET", "/api/onboarding/state")[0] == 401
        parts = urlsplit(app.login_url())
        status, headers, _ = raw("GET", f"{parts.path}?{parts.query}")
        cookie = headers["set-cookie"].split(";")[0]
        status, _, body = raw("GET", "/api/onboarding/state", {"Cookie": cookie})
        assert status == 200 and json.loads(body)["complete"] is False
        change = json.dumps({"step": "welcome", "status": "done"})
        assert raw("POST", "/api/onboarding/step", {"Cookie": cookie}, change)[0] == 403  # no Origin, no X-Alfred
        allowed = {"Cookie": cookie, "Origin": app.origin, "X-Alfred": "1", "Content-Type": "application/json"}
        assert raw("POST", "/api/onboarding/step", allowed, change)[0] == 200
        assert raw("PUT", "/api/onboarding/autostart", {"Cookie": cookie}, json.dumps({"on": True}))[0] == 403

        status, headers, page = raw("GET", "/onboarding/")
        assert status == 200 and headers["content-type"].startswith("text/html") and headers["content-security-policy"] == CSP
        assert b'src="/onboarding/onboarding.js"' in page and b'href="/theme/aero.css"' in page
        status, headers, _ = raw("GET", "/onboarding")
        assert status == 303 and headers["location"] == "/onboarding/"
        for path, kind in (("/onboarding/onboarding.js", "text/javascript"), ("/onboarding/onboarding.css", "text/css"),
                           ("/theme/aero.css", "text/css"), ("/onboarding/settings-card.js", "text/javascript")):
            status, headers, _ = raw("GET", path)
            assert status == 200 and headers["content-type"].startswith(kind), path
        assert raw("GET", "/onboarding/", {"Host": f"evil.example:{app.port}"})[0] == 403
    finally:
        service.shutdown()
        app.close()


def test_ui_files_follow_the_page_rules():
    """No inline scripts or styles (the CSP forbids them), nothing from the internet, never innerHTML."""
    folder = UI_DIR / "onboarding"
    for page in [folder / "index.html", UI_DIR / "index.html"]:
        html = page.read_text(encoding="utf-8")
        assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), page
        assert not re.search(r"\son[a-z]+\s*=", html) and " style=" not in html and "<style" not in html, page
        assert not re.search(r'<(script|link|img)[^>]+(src|href)="(https?:)?//', html), page
    for script in [folder / "onboarding.js", folder / "settings-card.js", UI_DIR / "app.js"]:
        code = script.read_text(encoding="utf-8")
        assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|\beval\(|new Function", code), script
    for sheet in [UI_DIR / "theme" / "aero.css", folder / "onboarding.css"]:
        css = sheet.read_text(encoding="utf-8")
        assert "@import" not in css and not re.search(r"url\(\s*['\"]?(https?:)?//", css), sheet
    aero = (UI_DIR / "theme" / "aero.css").read_text(encoding="utf-8")
    for part in (".aero-panel", ".aero-button", ".aero-input", ".aero-toggle", ".aero-progress", ".aero-steps",
                 ".aero-stop", "prefers-reduced-motion", "prefers-color-scheme: dark", "Segoe UI"):
        assert part in aero, part
    assert "/api/onboarding/state" in (UI_DIR / "app.js").read_text(encoding="utf-8")  # first run goes to the setup


def test_setup_imports_nothing_optional():
    """Windows-only and optional modules load only when a step uses them."""
    import subprocess

    code = ("import sys, jarvis.app.server, jarvis.onboarding.routes, jarvis.onboarding.speech;"
            "print([m for m in ('sounddevice', 'huggingface_hub', 'faster_whisper', 'webview', 'pystray', 'anthropic',"
            " 'win32cred', 'win32job') if m in sys.modules])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                         cwd=str(Path(__file__).resolve().parent.parent))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"
