"""The Alfred app's local server and shell (jarvis/app/server.py, main.py) over FakeService.
No window, tray or Windows needed: pywebview and pystray are never imported here."""

import ctypes
import functools
import http.client
import json
import os
import re
import sys
import threading
import time
import types
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import config, consent, selftest  # noqa: E402
from jarvis.app import main as app_main  # noqa: E402
from jarvis.app.server import CSP, UI_DIR, App, tail_jsonl  # noqa: E402
from jarvis.service import AlfredService, QueueFull  # noqa: E402
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
    """Talks to the app like its window: signs in once with the one-time link, then sends the key
    (X-Alfred) on every call and its Origin on changes."""

    def __init__(self, app):
        self.app = app
        self.key = ""

    def raw(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.app.port, timeout=10)
        data = json.dumps(body) if body is not None else None
        conn.request(method, path, body=data, headers=headers or {})
        resp = conn.getresponse()
        out = resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
        conn.close()
        return out

    def sign_in(self) -> "Client":
        status, headers, page = self.raw("GET", _path(self.app.login_url()))
        assert status == 200 and headers["cache-control"] == "no-store" and "set-cookie" not in headers
        self.key = re.search(rb'<meta name="alfred-key" content="([^"]+)">', page).group(1).decode()
        return self

    def call(self, method, path, body=None, **headers):
        sent = {"X-Alfred": self.key}
        if method != "GET":
            sent.update({"Origin": self.app.origin, "Content-Type": "application/json"})
            body = {} if body is None else body
        sent.update({k.replace("_", "-"): v for k, v in headers.items()})
        status, _, data = self.raw(method, path, body, {k: v for k, v in sent.items() if v is not None})
        return status, json.loads(data) if data else None

    def events(self, until, after=0, last_event_id=None, timeout=10.0):
        """Read the event feed until until(event) is true. Checks each SSE id is the event's seq."""
        headers = {"X-Alfred": self.key}
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
    assert client.call("GET", "/api/status")[0] == 401  # no key
    assert client.raw("GET", "/auth?code=wrong")[0] == 403

    # The one-time link answers with the page and the key in it, for the page to keep per origin
    # (sessionStorage). Never a cookie: browsers send those to every port of 127.0.0.1.
    url = _path(app.login_url())
    status, headers, page = client.raw("GET", url)
    assert status == 200 and headers["cache-control"] == "no-store" and "set-cookie" not in headers
    assert headers["content-type"].startswith("text/html") and b'src="app.js"' in page
    assert f'<head><meta name="alfred-key" content="{app.secret}">'.encode() in page
    assert app.secret.encode() not in client.raw("GET", "/")[2]
    client.key = app.secret
    assert client.raw("GET", url)[0] == 403  # one time only

    stale = _path(app.login_url())
    app._codes[stale.split("code=")[1]] = 0  # expired
    assert client.raw("GET", stale)[0] == 403

    status, body = client.call("GET", "/api/status")
    assert status == 200 and body["engine"] == "ready"
    for wrong in ("x" * 43, "1", ""):
        assert Client(app).call("GET", "/api/status", X_Alfred=wrong)[0] == 401
    # Only the header counts: not a cookie (it would reach other local servers) nor the URL.
    assert Client(app).call("GET", "/api/status", Cookie=f"alfred={app.secret}")[0] == 401
    assert Client(app).call("GET", f"/api/status?key={app.secret}")[0] == 401

    # Host must be this server on every route, health and the page included: a DNS-rebinding page
    # sends its own name.
    for path in ("/api/status", "/api/health", "/", "/app.js", "/auth?code=x", "/api/nothing"):
        assert client.call("GET", path, Host=f"evil.example:{port}")[0] == 403, path
    assert client.call("POST", "/api/instance/show", Host=f"evil.example:{port}",
                       X_Alfred_Instance=app.instance_token)[0] == 403
    assert client.call("GET", "/api/status", Host="127.0.0.1:1")[0] == 403
    assert client.call("GET", "/api/status", Host=f"localhost:{port}")[0] == 200

    # Changes need the app's Origin as well as the key.
    assert client.call("POST", "/api/stop", Origin="http://evil.example")[0] == 403
    assert client.call("POST", "/api/stop", Origin=None)[0] == 403
    assert client.call("POST", "/api/stop", X_Alfred=None)[0] == 401
    assert client.call("POST", "/api/stop", X_Alfred="1")[0] == 401
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
    status, _, raw = client.raw("GET", "/api/settings", headers={"X-Alfred": client.key})
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
    _, _, raw = client.raw("GET", "/api/settings", headers={"X-Alfred": client.key})
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
    assert "new EventSource(" not in js and "document.cookie" not in js  # neither can carry the key
    assert '"X-Alfred": key' in js and "last_error" in js
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


def test_an_engine_that_cant_start_says_why(env):
    consent.record("test")
    store = Store(env / "settings.env", MemoryBackend())
    service = AlfredService(store, engine_cmd=[str(env / "missing" / "alfred-cli.exe"), "--engine"])
    app = App(service, store=store, keepalive=0.2).start()
    try:
        app.start_service()
        client = Client(app).sign_in()
        status = client.call("GET", "/api/status")[1]
        assert status["engine"] == "crashed" and "Couldn't start Alfred's engine" in status["last_error"]
        after = service.bus.last_seq
        assert client.call("POST", "/api/restart")[0] == 200
        assert service.events(after, 0) == []  # no new event says it failed again: the window asks
        status = client.call("GET", "/api/status")[1]
        assert status["engine"] == "crashed" and "Couldn't start Alfred's engine" in status["last_error"]
    finally:
        service.shutdown(2)
        app.close()


def test_local_approvals_bring_the_window_forward(app):
    class Window:
        shown = 0

        def show(self):
            Window.shown += 1

        def restore(self):
            pass

    shell = app_main.Shell(app, app.service)
    shell.window = Window()
    shell._on_event({"type": "approval", "source": "telegram", "reason": "send it"})  # answered in Telegram
    shell._on_event({"type": "approval", "source": "remote", "reason": "copy a file here"})
    deadline = time.monotonic() + 5
    while not Window.shown and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.1)
    assert Window.shown == 1


class FakeEvent:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self

    def set(self):
        return [handler() for handler in self.handlers]


class FakeWindow:
    def __init__(self, url):
        self.url, self.calls = url, []
        self.events = SimpleNamespace(**{name: FakeEvent() for name in ("closing", "minimized", "restored", "maximized")})

    def __getattr__(self, name):  # show, restore, hide, destroy
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda: self.calls.append(name)


def _fake_webview(monkeypatch, renderer):
    """pywebview as far as main.py uses it, with the engine pywebview would pick on Windows."""
    windows = []
    webview = types.ModuleType("webview")
    webview.create_window = lambda title, url, **kw: windows.append(FakeWindow(url)) or windows[-1]
    webview.start = lambda: None
    webview.platforms = types.ModuleType("webview.platforms")
    webview.platforms.winforms = types.ModuleType("webview.platforms.winforms")
    webview.platforms.winforms.renderer = renderer
    for module in (webview, webview.platforms, webview.platforms.winforms):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(selftest, "webview2_version", lambda: "131.0.2903.70" if renderer == "edgechromium" else "")
    monkeypatch.setattr(app_main, "_missing_webview2", functools.partial(app_main._missing_webview2, "win32"))
    return windows


def test_window_show_minimize_close_and_quit(app, monkeypatch):
    opened, notes = [], []
    monkeypatch.setattr(app_main, "_open_browser", opened.append)
    windows = _fake_webview(monkeypatch, "edgechromium")
    shell = app_main.Shell(app, app.service)
    shell.show()  # a second launch while the window is still opening: no stray browser tab
    assert opened == [] and windows == []

    assert shell._run_window() is True and not shell.window_failed
    window = shell.window
    assert window is windows[0] and "/auth?code=" in window.url
    shell.show()
    assert window.calls == ["show"]  # restore() would un-maximize it
    window.events.minimized.set()
    shell.show()
    assert window.calls == ["show", "show", "restore"]
    window.events.maximized.set()
    shell.show()
    assert window.calls[-1] == "show" and opened == []

    # With a tray icon, closing hides the window and says so once.
    shell.tray = SimpleNamespace(visible=True, notify=lambda text, title: notes.append(text))
    assert window.events.closing.set() == [False] and window.events.closing.set() == [False]
    assert not shell.quitting.is_set() and len(notes) == 1 and "tray" in notes[0]
    deadline = time.monotonic() + 5
    while window.calls.count("hide") < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert window.calls.count("hide") == 2

    # Windows signing out or shutting down: closing goes through, or Windows waits for Alfred.
    monkeypatch.setattr(app_main, "_session_ending", lambda: True)
    assert window.events.closing.set() == [True] and shell.quitting.is_set()

    shell.quit()
    assert window.calls[-1] == "destroy"
    shell.show()
    assert window.calls[-1] == "destroy" and opened == []


def test_closing_without_a_tray_quits(app):
    shell = app_main.Shell(app, app.service)
    assert shell._on_closing() is True and shell.quitting.is_set()
    shell = app_main.Shell(app, app.service)
    shell.tray = SimpleNamespace(visible=False)
    assert shell._on_closing() is True and shell.quitting.is_set()


def test_without_webview2_alfred_uses_the_browser(app, monkeypatch):
    """pywebview doesn't raise without the WebView2 runtime: it quietly uses Internet Explorer,
    which can't run Alfred's page."""
    opened = []
    monkeypatch.setattr(app_main, "_open_browser", opened.append)
    windows = _fake_webview(monkeypatch, "mshtml")
    assert app_main._missing_webview2() and not app_main._missing_webview2.func("linux")
    shell = app_main.Shell(app, app.service)
    assert shell._run_window() is False and shell.window_failed and windows == []
    shell.show()
    assert len(opened) == 1 and "/auth?code=" in opened[0]


def test_pywebviews_backend_is_left_alone_without_webview2(monkeypatch):
    """Importing pywebview's Windows backend on a PC without WebView2 writes Internet Explorer settings
    into the registry, so the registry is asked first."""
    missing_webview2 = functools.partial(app_main._missing_webview2, "win32")
    _fake_webview(monkeypatch, "edgechromium")
    winforms = sys.modules.pop("webview.platforms.winforms")
    del sys.modules["webview.platforms"].winforms  # importing it now raises ImportError
    monkeypatch.setattr(selftest, "webview2_version", lambda: "")
    assert missing_webview2()

    def unreadable():
        raise OSError("registry unreadable")

    sys.modules["webview.platforms"].winforms = sys.modules["webview.platforms.winforms"] = winforms
    monkeypatch.setattr(selftest, "webview2_version", unreadable)
    assert missing_webview2() is False  # pywebview's own answer decides


def test_stop_hotkey_only_posts_to_its_running_thread(monkeypatch):
    posted = []

    class User32:
        def __init__(self, registered):
            self.registered, self.quit = registered, threading.Event()

        def RegisterHotKey(self, *args):
            return self.registered

        def UnregisterHotKey(self, *args):
            return 1

        def GetMessageW(self, *args):
            self.quit.wait(5)
            return 0

        def PostThreadMessageW(self, thread_id, message, *args):
            posted.append((thread_id, message))
            self.quit.set()

    def windll(registered):
        user32 = User32(registered)
        monkeypatch.setattr(ctypes, "windll", SimpleNamespace(
            user32=user32, kernel32=SimpleNamespace(GetCurrentThreadId=lambda: 4242)), raising=False)
        return user32

    def ended(hotkey):
        deadline = time.monotonic() + 5
        while hotkey.ok and time.monotonic() < deadline:
            time.sleep(0.02)
        return not hotkey.ok

    windll(0)  # another app owns the shortcut
    hotkey = app_main.StopHotkey(lambda: None).start()
    assert not hotkey.ok and "taken by another app" in hotkey.error
    hotkey.stop()
    assert posted == []  # its thread is gone, and Windows reuses thread ids

    windll(1)
    hotkey = app_main.StopHotkey(lambda: None).start()
    assert hotkey.ok and not hotkey.error
    hotkey.stop()
    assert posted == [(4242, app_main.WM_QUIT)] and ended(hotkey)
    hotkey.stop()
    assert len(posted) == 1


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
    opened, shells, trays, hotkeys = [], [], [], []
    monkeypatch.setattr(app_main, "_open_browser", opened.append)

    class Shell(app_main.Shell):  # no real tray icon or global hotkey on the machine running the tests
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            shells.append(self)

        def _start_tray(self):
            trays.append(self)

        def _run_window(self):
            raise AssertionError("--no-window opened a window")

    class Hotkey:
        keys, ok, error = app_main.HOTKEY, False, "off in tests"

        def __init__(self, on_press):
            hotkeys.append(on_press)

        def start(self):
            return self

        def stop(self):
            pass

    monkeypatch.setattr(app_main, "Shell", Shell)
    monkeypatch.setattr(app_main, "StopHotkey", Hotkey)
    result = []
    runner = threading.Thread(target=lambda: result.append(app_main.main(["--fake-core", "--no-window"])), daemon=True)
    runner.start()
    try:
        deadline = time.monotonic() + 10
        while not opened and time.monotonic() < deadline:
            time.sleep(0.05)
        assert opened, "the browser was never opened"
        assert len(trays) == 1 and len(hotkeys) == (sys.platform == "win32") and shells[0].window_failed

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
    finally:
        for shell in shells:
            shell.quit()
        runner.join(10)
    assert not (config.data_dir() / "run" / "alfred.json").exists()
    with pytest.raises(OSError):
        http.client.HTTPConnection("127.0.0.1", port, timeout=2).request("GET", "/api/health")


def test_tray_icon_is_the_logo_and_survives_a_missing_file(tmp_path):
    from PIL import Image, ImageDraw

    logo = app_main.tray_image(Image, ImageDraw)
    assert logo.size == (64, 64) and logo.mode == "RGBA"
    assert logo.getpixel((0, 0))[3] == 0 and logo.getpixel((32, 32))[3] == 255  # round, on a clear background
    assert app_main.LOGO.is_file() and (UI_DIR / "logo.png").is_file()
    fallback = app_main.tray_image(Image, ImageDraw, logo=tmp_path / "missing.png")
    assert fallback.size == (64, 64) and fallback.getpixel((32, 32))[3] == 255


def test_error_log_keeps_the_route_and_place_but_not_secret_messages(env):
    from jarvis.app.http import Request
    from jarvis.app.server import log_error

    secret = "sk-" + "ant-api03-not-for-the-log"  # a value at run time, as keys are; never in the source
    try:
        {}[secret]
    except KeyError as e:
        log_error(Request("POST", "/api/onboarding/openai"), e)
    try:
        __import__("no_such_alfred_part")
    except ImportError as e:
        log_error(Request("GET", "/api/onboarding/settings"), e)
    text = (config.data_dir() / "errors.log").read_text(encoding="utf-8")
    assert "POST /api/onboarding/openai" in text and "KeyError" in text and "test_app_server.py" in text
    assert "sk-ant" not in text
    assert "No module named 'no_such_alfred_part'" in text
