"""The Alfred app's local server and shell (jarvis/app/server.py, main.py) over FakeService.
No window, tray or Windows needed: pywebview and pystray are never imported here."""

import http.client
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import config, consent  # noqa: E402
from jarvis.app import main as app_main  # noqa: E402
from jarvis.app.server import CSP, UI_DIR, App, tail_jsonl  # noqa: E402
from jarvis.service import QueueFull  # noqa: E402
from jarvis.service.fake import FakeService  # noqa: E402
from jarvis.settings import MemoryBackend, Store  # noqa: E402

KEY = "sk-ant-api03-abcdefghijklmnop-9f2c"


@pytest.fixture
def env(tmp_path):
    saved = dict(os.environ)
    os.environ["LOCALAPPDATA"] = str(tmp_path)
    for key in ("JARVIS_ACCEPT_TERMS", "JARVIS_SETTINGS_FILE", "JARVIS_IGNORE_USER_SETTINGS"):
        os.environ.pop(key, None)
    yield tmp_path
    os.environ.clear()
    os.environ.update(saved)


def _app(env, *, consented=True, ui_dir=None, secrets=None):
    if consented:
        consent.record("test")
    store = Store(env / "settings.env", MemoryBackend(secrets or {"ANTHROPIC_API_KEY": KEY}))
    service = FakeService(store, delay=0.01, consent=None)
    app = App(service, store=store, ui_dir=ui_dir, keepalive=0.2).start()
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
    """Talks to the app like its window: signs in once, then sends the cookie, Origin and X-Alfred."""

    def __init__(self, app):
        self.app = app
        self.cookie = ""

    def raw(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=10)
        data = json.dumps(body) if body is not None else None
        conn.request(method, path, body=data, headers=headers or {})
        resp = conn.getresponse()
        out = resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
        conn.close()
        return out

    def sign_in(self) -> "Client":
        status, headers, _ = self.raw("GET", _path(self.app.login_url()))
        assert status == 303 and headers["location"] == "/"
        self.cookie = headers["set-cookie"].split(";")[0]
        return self

    def call(self, method, path, body=None, **headers):
        sent = {"Cookie": self.cookie}
        if method != "GET":
            sent.update({"Origin": self.app.origin, "X-Alfred": "1", "Content-Type": "application/json"})
            body = {} if body is None else body
        sent.update({k.replace("_", "-"): v for k, v in headers.items()})
        status, _, data = self.raw(method, path, body, {k: v for k, v in sent.items() if v is not None})
        return status, json.loads(data) if data else None

    def events(self, until, after=0, last_event_id=None, timeout=10.0):
        """Read the event feed until until(event) is true. Checks each SSE id is the event's seq."""
        headers = {"Cookie": self.cookie}
        path = "/api/events"
        if last_event_id is not None:
            headers["Last-Event-ID"] = str(last_event_id)
        elif after is not None:
            path += f"?after={after}"
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=timeout)
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        assert resp.status == 200 and resp.getheader("Content-Type") == "text/event-stream"
        events, sse_id, deadline = [], None, time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                line = resp.fp.readline().decode()
                if not line:
                    break
                if line.startswith("id: "):
                    sse_id = int(line[4:])
                elif line.startswith("data: "):
                    event = json.loads(line[6:])
                    assert event["seq"] == sse_id
                    events.append(event)
                    if until(event):
                        return events
                elif line.startswith(":") and until({"type": "keep-alive"}):
                    return events
        finally:
            conn.close()
        raise AssertionError(f"event not seen; got {[e['type'] for e in events]}")


def _path(url: str) -> str:
    parts = urlsplit(url)
    return parts.path + "?" + parts.query


def test_local_only_auth(app):
    client = Client(app)
    port = app.port
    assert client.raw("GET", "/api/health")[0] == 200
    assert client.call("GET", "/api/status")[0] == 401  # no cookie
    assert client.raw("GET", "/auth?code=wrong")[0] == 403

    url = _path(app.login_url())
    status, headers, _ = client.raw("GET", url)
    assert status == 303 and headers["location"] == "/"
    cookie = headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and "Path=/" in cookie
    client.cookie = cookie.split(";")[0]
    assert client.raw("GET", url)[0] == 403  # one time only

    stale = _path(app.login_url())
    app._codes[stale.split("code=")[1]] = 0  # expired
    assert client.raw("GET", stale)[0] == 403

    status, body = client.call("GET", "/api/status")
    assert status == 200 and body["engine"] == "ready"
    assert Client(app).call("GET", "/api/status", Cookie="alfred=" + "x" * 43)[0] == 401

    # Host must be this server: a DNS-rebinding page sends its own name.
    assert client.call("GET", "/api/status", Host=f"evil.example:{port}")[0] == 403
    assert client.call("GET", "/api/status", Host="127.0.0.1:1")[0] == 403
    assert client.call("GET", "/api/status", Host=f"localhost:{port}")[0] == 200
    assert client.raw("GET", "/", headers={"Host": f"evil.example:{port}"})[0] == 403

    # Changes need the app's Origin and X-Alfred: 1 as well as the cookie.
    assert client.call("POST", "/api/stop", Origin="http://evil.example")[0] == 403
    assert client.call("POST", "/api/stop", Origin=None)[0] == 403
    assert client.call("POST", "/api/stop", X_Alfred=None)[0] == 403
    assert client.call("POST", "/api/stop", X_Alfred="0")[0] == 403
    assert client.call("POST", "/api/stop", Host=f"localhost:{port}", Origin=f"http://localhost:{port}")[0] == 200
    assert Client(app).call("POST", "/api/stop")[0] == 401


def test_second_launch_shows_the_window(app):
    shown = []
    app.on_show = lambda: shown.append(True)
    client = Client(app)
    assert client.raw("POST", "/api/instance/show", {}, {"X-Alfred-Instance": "nope"})[0] == 403
    assert not shown
    assert client.raw("POST", "/api/instance/show", {}, {"X-Alfred-Instance": app.instance_token})[0] == 200
    assert shown == [True]


def test_command_reply_and_done_over_sse(app):
    client = Client(app).sign_in()
    status, body = client.call("POST", "/api/command", {"text": "hello there"})
    assert status == 200
    cid = body["id"]
    events = client.events(lambda e: e["type"] == "done" and e["id"] == cid)
    kinds = [e["type"] for e in events if e.get("id") == cid]
    assert kinds[0] == "command" and "reply" in kinds and kinds[-1] == "done"
    reply = next(e for e in events if e["type"] == "reply")
    assert reply["text"] == "(fake) You said: hello there" and reply["source"] == "desktop"
    assert events[-1]["ok"] is True

    # Resume after the reply with Last-Event-ID (what EventSource sends when it reconnects).
    again = client.events(lambda e: True, last_event_id=reply["seq"])
    assert again[0]["seq"] == reply["seq"] + 1
    # Nothing new: a keep-alive comment arrives instead (events() raises if it doesn't).
    client.events(lambda e: e["type"] == "keep-alive", after=app.service.bus.last_seq, timeout=5)

    assert client.call("POST", "/api/command", {"text": "   "})[0] == 400
    assert client.call("POST", "/api/command", {"text": 5})[0] == 400
    assert client.call("POST", "/api/command", None)[0] == 400


def test_not_ready_and_queue_full_are_friendly(env):
    app = _app(env, consented=False)
    try:
        client = Client(app).sign_in()
        status, body = client.call("POST", "/api/command", {"text": "hi"})
        assert status == 409 and "Alfred" in body["error"]

        def full(text, source="desktop"):
            raise QueueFull("The command queue is full. Wait for a reply before sending more.")

        app.service.submit = full
        status, body = client.call("POST", "/api/command", {"text": "hi"})
        assert status == 429 and "queue is full" in body["error"]
    finally:
        app.service.shutdown()
        app.close()


def test_stop(app):
    client = Client(app).sign_in()
    cid = client.call("POST", "/api/command", {"text": "long task"})[1]["id"]
    seen = client.events(lambda e: e["type"] == "status" and e.get("kind") == "tool" and e["id"] == cid)
    status, body = client.call("POST", "/api/stop")
    assert status == 200 and body == {"stopped": True}
    events = client.events(lambda e: e["type"] == "ready" and e["after_stop"], after=seen[-1]["seq"])
    assert {"type": "stop", "source": "desktop"}.items() <= next(e for e in events if e["type"] == "stop").items()
    done = next(e for e in events if e["type"] == "done")
    assert done["id"] == cid and done["stopped"] is True
    assert app.service.wait_ready(5)


def test_approval_round_trip(app):
    client = Client(app).sign_in()
    cid = client.call("POST", "/api/command", {"text": "send the report"})[1]["id"]
    asked = client.events(lambda e: e["type"] == "approval")[-1]
    assert asked["id"] == cid and asked["source"] == "desktop" and "Send" in asked["reason"]
    status = client.call("GET", "/api/status")[1]
    assert [a["token"] for a in status["approvals"]] == [asked["token"]]

    assert client.call("POST", "/api/approval", {"approved": True})[0] == 400
    status, body = client.call("POST", "/api/approval", {"token": asked["token"], "approved": True})
    assert status == 200 and body == {"ok": True}
    events = client.events(lambda e: e["type"] == "done" and e["id"] == cid, after=asked["seq"])
    closed = next(e for e in events if e["type"] == "approval_closed")
    assert closed["approved"] is True and closed["by"] == "desktop"
    assert next(e for e in events if e["type"] == "reply")["text"] == "Done, sent it."
    status, body = client.call("POST", "/api/approval", {"token": asked["token"], "approved": False})
    assert status == 409 and "expired" in body["error"]


def test_settings_never_return_secrets_and_only_take_allowed_keys(app):
    client = Client(app).sign_in()
    status, _, raw = client.raw("GET", "/api/settings", headers={"Cookie": client.cookie})
    assert status == 200 and KEY.encode() not in raw
    body = json.loads(raw)
    assert body["secrets"]["ANTHROPIC_API_KEY"] == {"set": True, "hint": "sk-ant-...9f2c"}
    assert body["secrets"]["OPENAI_API_KEY"] == {"set": False, "hint": ""}
    assert "JARVIS_MODEL" in body["editable"] and body["defaults"]["JARVIS_EFFORT"] == "low"

    after = app.service.bus.last_seq
    status, body = client.call("PUT", "/api/settings", {"JARVIS_MODEL": "claude-test-1", "JARVIS_SPEAK": False})
    assert status == 200 and body == {"saved": ["JARVIS_MODEL", "JARVIS_SPEAK"], "restarting": True}
    assert app.store.get("JARVIS_MODEL") == "claude-test-1" and app.store.get("JARVIS_SPEAK") == "0"
    client.events(lambda e: e["type"] == "state" and e["state"] == "starting", after=after)
    assert app.service.wait_ready(5)
    assert client.call("PUT", "/api/settings", {"JARVIS_MODEL": "claude-test-1"})[1]["saved"] == []
    assert client.call("PUT", "/api/settings", {"TELEGRAM_OWNER_ID": 12345})[1] == {
        "saved": ["TELEGRAM_OWNER_ID"], "restarting": False}  # only Telegram reads it

    for bad in ({"ANTHROPIC_API_KEY": "sk-x"}, {"JARVIS_SERVE": "1"}, {"JARVIS_EFFORT": "extreme"},
                {"JARVIS_NAME": "two\nlines"}, {"JARVIS_SPEAK": "yes"}, {"JARVIS_MAX_STEPS": 500},
                {"JARVIS_MODEL": ["a"]}, {"JARVIS_EFFORT": "medium", "JARVIS_DEVICES": "x=1.2.3.4"}, {}):
        status, body = client.call("PUT", "/api/settings", bad)
        assert status == 400 and body["error"], bad
    assert app.store.get("JARVIS_EFFORT") is None  # a bad batch saves nothing
    assert client.call("PUT", "/api/settings", {"JARVIS_MODEL": None})[1]["saved"] == ["JARVIS_MODEL"]
    assert app.store.get("JARVIS_MODEL") is None


def test_secrets_are_write_only(app):
    client = Client(app).sign_in()
    value = "sk-proj-abcdefghijklmnopqrstuvwx-7a1b"
    status, body = client.call("PUT", "/api/secrets/OPENAI_API_KEY", {"value": value})
    assert status == 200 and body["set"] is True and body["hint"].endswith("7a1b") and value not in json.dumps(body)
    assert body["restarting"] is False  # only the Telegram bridge uses it
    assert app.store.get_secret("OPENAI_API_KEY") == value
    _, _, raw = client.raw("GET", "/api/settings", headers={"Cookie": client.cookie})
    assert value.encode() not in raw
    assert client.call("GET", "/api/secrets/OPENAI_API_KEY")[0] == 405  # there is no way to read one

    status, body = client.call("PUT", "/api/secrets/ANTHROPIC_API_KEY", {"value": "sk-ant-new-key-0000000000-1111"})
    assert status == 200 and body["restarting"] is True
    assert client.call("PUT", "/api/secrets/OPENAI_API_KEY", {"value": "  "})[0] == 400
    assert client.call("PUT", "/api/secrets/OPENAI_API_KEY", {"value": "a\nb"})[0] == 400
    assert client.call("PUT", "/api/secrets/JARVIS_MODEL", {"value": "x"})[0] == 404
    assert client.call("DELETE", "/api/secrets/JARVIS_MODEL")[0] == 404

    status, body = client.call("DELETE", "/api/secrets/OPENAI_API_KEY")
    assert status == 200 and body["set"] is False and not app.store.has_secret("OPENAI_API_KEY")


def test_audit_tail(app, env):
    client = Client(app).sign_in()
    assert client.call("GET", "/api/audit")[1]["entries"] == []
    lines = [json.dumps({"ts": f"2026-10-10T10:00:0{i}", "tool": f"t{i}", "args": {}, "ok": True, "result": "ok"})
             for i in range(5)]
    audit = config.data_dir() / "audit.jsonl"
    audit.write_text("\n".join(lines[:3] + ["{broken"] + lines[3:]) + "\n", encoding="utf-8")
    status, body = client.call("GET", "/api/audit?limit=3")
    assert status == 200 and [e["tool"] for e in body["entries"]] == ["t4", "t3", "t2"]
    assert body["file"].endswith("audit.jsonl")
    assert len(client.call("GET", "/api/audit?limit=nope")[1]["entries"]) == 5

    big = env / "big.jsonl"
    big.write_text("".join(json.dumps({"n": i, "pad": "x" * 100}) + "\n" for i in range(3000)), encoding="utf-8")
    assert [e["n"] for e in tail_jsonl(big, 4)] == [2999, 2998, 2997, 2996]
    assert len(tail_jsonl(big, 1000)) == 1000
    assert tail_jsonl(env / "missing.jsonl", 5) == []


def test_consent_flow(env):
    app = _app(env, consented=False)
    try:
        client = Client(app).sign_in()
        assert client.call("GET", "/api/status")[1]["engine"] == "needs_consent"
        status, body = client.call("GET", "/api/consent")
        assert status == 200 and body["accepted"] is False and "Alfred" in body["notice_markdown"]
        assert client.call("POST", "/api/consent", {"version": "1999-01-01"})[0] == 409
        assert not consent.accepted()

        status, body = client.call("POST", "/api/consent", {"version": body["version"]})
        assert status == 200 and body["accepted"] is True
        assert consent.accepted()
        assert json.loads((config.data_dir() / "consent.json").read_text())["source"] == "app"
        assert app.service.wait_ready(5)
        assert client.call("GET", "/api/consent")[1]["accepted"] is True
    finally:
        app.service.shutdown()
        app.close()


def test_static_files_csp_and_no_traversal(env, tmp_path):
    ui = tmp_path / "ui"
    (ui / "sub").mkdir(parents=True)
    (ui / "index.html").write_text("<!doctype html><title>t</title>", encoding="utf-8")
    (ui / "sub" / "a.js").write_text("1", encoding="utf-8")
    (ui / ".secret.js").write_text("1", encoding="utf-8")
    (ui / "notes.txt").write_text("1", encoding="utf-8")
    (tmp_path / "secret.html").write_text("SECRET", encoding="utf-8")
    app = _app(env, ui_dir=ui)
    try:
        client = Client(app)
        status, headers, body = client.raw("GET", "/")
        assert status == 200 and body.startswith(b"<!doctype html>")
        assert headers["content-security-policy"] == CSP and "script-src 'self'" in CSP and "unsafe" not in CSP
        assert headers["x-content-type-options"] == "nosniff" and headers["cache-control"] == "no-store"
        assert headers["content-type"].startswith("text/html")
        status, headers, _ = client.raw("GET", "/sub/a.js")
        assert status == 200 and headers["content-type"].startswith("text/javascript")
        assert headers["content-security-policy"] == CSP and headers["cache-control"] == "no-cache"
        for path in ("/../secret.html", "/%2e%2e/secret.html", "/..%2fsecret.html", "/sub/../../secret.html",
                     "/sub/%2e%2e/%2e%2e/secret.html", "/%5c..%5csecret.html", "/..%5csecret.html",
                     "/C:%5cWindows%5cwin.ini", "/index.html%00.js", "/.secret.js", "/notes.txt", "/sub",
                     "/sub/", "//etc/passwd", "/api/nothing"):
            status, _, body = client.raw("GET", path)
            assert status == 404 and b"SECRET" not in body, path
        assert client.raw("POST", "/index.html", {})[0] == 404
    finally:
        app.service.shutdown()
        app.close()


def test_real_ui_files_follow_the_csp(app):
    client = Client(app)
    html = (UI_DIR / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)", html)  # no inline scripts
    assert not re.search(r"\son[a-z]+\s*=", html) and " style=" not in html  # no inline handlers or styles
    assert "http://" not in html and "https://" not in html  # no CDNs
    js = (UI_DIR / "app.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "eval(" not in js and "https://" not in js
    for name in re.findall(r'(?:src|href)="([^"]+)"', html):
        assert client.raw("GET", "/" + name)[0] == 200, name


def test_telegram_reset_restart_and_quit(app):
    client = Client(app).sign_in()
    status, body = client.call("POST", "/api/telegram", {"on": True})
    assert status == 200 and body["state"] == "running" and app.store.get("JARVIS_TELEGRAM") == "1"
    assert client.call("POST", "/api/telegram", {"on": False})[1]["state"] == "off"
    assert app.store.get("JARVIS_TELEGRAM") == "0"
    assert client.call("GET", "/api/settings")[1]["telegram"] is False

    after = app.service.bus.last_seq
    assert client.call("POST", "/api/reset")[0] == 200
    client.events(lambda e: e["type"] == "notice" and "fresh start" in e["text"], after=after)
    assert client.call("POST", "/api/restart")[0] == 200
    assert app.service.wait_ready(5)

    assert client.call("GET", "/api/status")[1]["quit"] is False
    assert client.call("POST", "/api/quit")[0] == 409
    quit_called = threading.Event()
    app.on_quit = quit_called.set
    assert client.call("POST", "/api/quit")[0] == 200
    assert quit_called.wait(5)


def test_local_approvals_bring_the_window_forward(app):
    class Window:
        shown = 0

        def show(self):
            Window.shown += 1

        def restore(self):
            pass

    shell = app_main.Shell(app, app.service, window=False)
    shell.window = Window()
    shell._on_event({"type": "approval", "source": "telegram", "reason": "send it"})  # answered in Telegram
    shell._on_event({"type": "approval", "source": "remote", "reason": "copy a file here"})
    deadline = time.monotonic() + 5
    while not Window.shown and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.1)
    assert Window.shown == 1


def test_instance_lock(tmp_path):
    first, second = app_main.InstanceLock(tmp_path / "run"), app_main.InstanceLock(tmp_path / "run")
    assert first.acquire()
    assert not second.acquire()
    first.publish(1234, "token")
    assert second.read()["port"] == 1234
    assert not second.show_running(timeout=0)  # nobody listens on 1234
    first.release()
    assert not (tmp_path / "run" / "alfred.json").exists()
    assert second.acquire()
    second.release()


def test_app_without_window_runs_shows_and_quits(env, monkeypatch):
    """python -m jarvis.app --fake-core --no-window, driven like a person would: open the link, a
    second launch shows Alfred again, Quit shuts everything down."""
    consent.record("test")
    opened = []
    monkeypatch.setattr(app_main, "_open_browser", opened.append)
    result = []
    runner = threading.Thread(target=lambda: result.append(app_main.main(["--fake-core", "--no-window"])), daemon=True)
    runner.start()
    deadline = time.monotonic() + 10
    while not opened and time.monotonic() < deadline:
        time.sleep(0.05)
    assert opened, "the browser was never opened"
    assert "webview" not in sys.modules and "pystray" not in sys.modules

    port = urlsplit(opened[0]).port
    window = SimpleNamespace(port=port, origin=f"http://127.0.0.1:{port}", login_url=lambda: opened[0])
    client = Client(window).sign_in()
    assert client.call("GET", "/api/status")[1]["engine"] in ("starting", "ready")
    assert not (config.data_dir() / "settings.env").exists()  # --fake-core keeps to its own settings

    assert app_main.main(["--fake-core", "--no-window"]) == 0  # a second launch shows the first
    assert len(opened) == 2 and opened[1] != opened[0]

    assert client.call("POST", "/api/quit")[0] == 200
    runner.join(10)
    assert not runner.is_alive() and result == [0]
    assert not (config.data_dir() / "run" / "alfred.json").exists()
    with pytest.raises(OSError):
        http.client.HTTPConnection("127.0.0.1", port, timeout=2).request("GET", "/api/health")
