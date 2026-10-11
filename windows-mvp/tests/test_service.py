"""AlfredService with the real engine process (jarvis/engine.py). No API key, no desktop actions:
the hidden test tools (JARVIS_TEST_TOOLS=1) stand in for slow or risky ones."""

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import config, consent  # noqa: E402
from jarvis.service import AlfredService, EventBus, NotReady, QueueFull, engine_command  # noqa: E402
from jarvis.service.fake import FakeService  # noqa: E402
from jarvis.settings import MemoryBackend, Store  # noqa: E402

FAKE_BRIDGE = r"""
import json, os, sys
def send(m): print(json.dumps(m), flush=True)
send({"type": "status", "state": "running"})
send({"type": "telegram_user", "id": os.environ.get("ALFRED_BRIDGE_MODE", "") + ":" + os.environ.get("TELEGRAM_BOT_TOKEN", "")})
send({"type": "command", "id": "n1", "text": "reset"})
for line in sys.stdin:
    print("got " + line.strip(), file=sys.stderr, flush=True)
    m = json.loads(line)
    if m.get("type") == "done" and m.get("id") == "n1":
        send({"type": "command", "id": "n2", "text": "_test_confirm photo"})
    if m.get("type") == "approval":
        send({"type": "approval", "token": m["token"], "approved": True})
"""


@pytest.fixture
def env(tmp_path):
    saved = dict(os.environ)
    os.environ.update({
        "LOCALAPPDATA": str(tmp_path), "ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": "",
        "JARVIS_ACCEPT_TERMS": consent.TERMS_VERSION, "JARVIS_IGNORE_USER_SETTINGS": "1",
        "JARVIS_TEST_TOOLS": "1", "JARVIS_SPEAK": "0", "JARVIS_TOKEN": "", "JARVIS_SERVE": "0",
        "JARVIS_DEVICES": "", "TELEGRAM_BOT_TOKEN": "", "SPECTRUM_PROJECT_SECRET": "",
    })
    yield tmp_path
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def svc(env):
    service = AlfredService(Store(env / "settings.env", MemoryBackend()))
    yield service
    service.shutdown(5)


def _started(service) -> AlfredService:
    service.start()
    assert service.wait_ready(30), service.status()
    return service


def wait_for(service, match, after=0, timeout=20.0) -> dict:
    """The first event after seq `after` for which match(event) is true."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for event in service.events(after, timeout=max(0.0, deadline - time.monotonic())):
            after = event["seq"]
            if match(event):
                return event
    raise AssertionError(f"no matching event; last: {service.events(None, 0)[-5:]}")


def _alive(pid: int) -> bool:
    try:
        import psutil
    except ImportError:
        psutil = None
    if psutil:
        try:
            return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except FileNotFoundError:
        return False


def test_a_command_gets_its_reply_and_done(svc):
    _started(svc)
    status = svc.status()
    assert status.engine == "ready" and not status.has_llm and status.pc_name
    cid = svc.submit("reset")
    reply = wait_for(svc, lambda e: e["type"] == "reply" and e["id"] == cid)
    assert reply["source"] == "desktop" and reply["text"] == "Okay, fresh start."
    done = wait_for(svc, lambda e: e["type"] == "done" and e["id"] == cid)
    assert done["ok"] is True and done["source"] == "desktop" and done["stopped"] is False
    result = svc.run_and_wait("reset", "voice", timeout=20)
    assert result.ok and result.replies == ["Okay, fresh start."] and not result.stopped and not result.timed_out
    assert svc.reset_conversation()
    wait_for(svc, lambda e: e["type"] == "notice" and e["text"] == "Okay, fresh start.")
    with pytest.raises(ValueError):
        svc.submit("   ")


def test_approval_round_trip_first_answer_wins(svc):
    _started(svc)
    cid = svc.submit("_test_confirm the email")
    asked = wait_for(svc, lambda e: e["type"] == "approval")
    assert asked["id"] == cid and asked["source"] == "desktop" and "_test_confirm" in asked["reason"]
    assert [a["token"] for a in svc.status().approvals] == [asked["token"]]
    assert svc.approve(asked["token"], True)
    assert not svc.approve(asked["token"], True)  # already answered
    closed = wait_for(svc, lambda e: e["type"] == "approval_closed")
    assert closed == {**closed, "token": asked["token"], "approved": True, "by": "desktop"}
    reply = wait_for(svc, lambda e: e["type"] == "reply" and e["id"] == cid)
    assert reply["text"] == "Confirmed the email."
    assert svc.status().approvals == []

    after = svc.bus.last_seq
    cid = svc.submit("_test_confirm the photo", source="telegram")
    asked = wait_for(svc, lambda e: e["type"] == "approval", after)
    assert asked["source"] == "telegram"
    assert svc.approve(asked["token"], False, by="telegram")
    reply = wait_for(svc, lambda e: e["type"] == "reply" and e["id"] == cid, after)
    assert "declined" in reply["text"]
    assert not svc.approve("deadbeef", True)


def test_stop_kills_the_whole_engine_tree_and_comes_back(svc, env):
    _started(svc)
    cid = svc.submit("_test_sleep 60 child")
    child = wait_for(svc, lambda e: e["type"] == "announce" and e["text"].startswith("child "))
    assert child["source"] == "desktop"
    pid = int(child["text"].split()[1])
    assert _alive(pid)
    start = time.monotonic()
    assert svc.stop()
    assert svc.stop()  # again while stopping: nothing more happens
    assert time.monotonic() - start < 1.0  # returns at once
    assert svc.status().engine in ("stopping", "starting")
    with pytest.raises(NotReady):
        svc.submit("reset")
    done = wait_for(svc, lambda e: e["type"] == "done" and e["id"] == cid)
    assert done["ok"] is False and done["stopped"] is True
    ready = wait_for(svc, lambda e: e["type"] == "ready" and e.get("after_stop"))
    assert ready["after_stop"] is True
    assert svc.wait_ready(20)
    assert '"tool": "desktop_stop"' in (env / "Jarvis" / "audit.jsonl").read_text(encoding="utf-8")
    deadline = time.monotonic() + 10
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(pid), "the engine's child process survived the hard stop"
    assert svc.run_and_wait("reset", timeout=20).ok


def test_needs_consent_until_accepted(svc):
    del os.environ["JARVIS_ACCEPT_TERMS"]
    assert svc.start().engine == "needs_consent"
    assert not svc.wait_ready(1)
    with pytest.raises(NotReady):
        svc.submit("reset")
    consent.record()
    _started(svc)


def test_events_replay_after_a_seq_and_long_poll(svc):
    _started(svc)
    svc.run_and_wait("reset", timeout=20)
    everything = svc.events(None, 0)
    assert [e["seq"] for e in everything] == list(range(1, len(everything) + 1))
    middle = everything[2]["seq"]
    assert svc.events(middle, 0) == everything[3:]
    last = svc.bus.last_seq
    start = time.monotonic()
    assert svc.events(last, timeout=0.2) == []
    assert time.monotonic() - start >= 0.15
    threading.Timer(0.2, lambda: svc.submit("reset")).start()
    woke = svc.events(last, timeout=10)
    assert woke and woke[0]["type"] == "command"


def test_event_bus_ring_buffer_and_subscribers():
    bus = EventBus(size=5)
    seen, broken = [], []
    unsubscribe = bus.subscribe(seen.append)
    bus.subscribe(lambda e: broken.append(1 / 0))  # a broken subscriber doesn't stop the feed
    for n in range(8):
        bus.publish("tick", n=n)
    assert [e["seq"] for e in bus.events(None, 0)] == [4, 5, 6, 7, 8]
    assert [e["seq"] for e in bus.events(2, 0)] == [4, 5, 6, 7, 8]  # older ones are gone
    assert [e["n"] for e in bus.events(6, 0)] == [6, 7]
    assert bus.events(99, 0) == bus.events(None, 0)  # a cursor from an older bus replays everything
    assert len(seen) == 8
    unsubscribe()
    bus.publish("tick", n=8)
    assert len(seen) == 8


def test_queue_is_bounded(svc):
    _started(svc)
    svc.submit("_test_sleep 60")
    for _ in range(19):
        svc.submit("reset")
    assert svc.status().queued == 19
    with pytest.raises(QueueFull):
        svc.submit("reset")
    svc.stop()
    assert svc.wait_ready(20)
    assert svc.status().queued == 0


def test_a_crashing_engine_is_restarted_then_given_up(env):
    service = AlfredService(Store(env / "settings.env", MemoryBackend()),
                            engine_cmd=[sys.executable, "-c", "import sys; print('boom', file=sys.stderr); sys.exit(1)"])
    service.RESTART_DELAYS = (0.05, 0.05, 0.05)
    try:
        service.start()
        deadline = time.monotonic() + 20
        while service.status().engine != "crashed" and time.monotonic() < deadline:
            time.sleep(0.05)
        status = service.status()
        assert status.engine == "crashed"
        assert "exit code 1" in status.last_error and "boom" in status.last_error
        crashes = [e for e in service.events(None, 0) if e["type"] == "crash"]
        assert [e["restart_in"] for e in crashes] == [0.05, 0.05, 0.05, None]  # three restarts, then it gives up
        assert service.start().engine in ("starting", "ready", "crashed")  # start() tries again from scratch
    finally:
        service.shutdown(2)


def test_restart_engine_starts_a_fresh_one(svc):
    _started(svc)
    after = svc.bus.last_seq
    svc.restart_engine("settings")
    ready = wait_for(svc, lambda e: e["type"] == "ready", after)
    assert ready["after_stop"] is False
    assert svc.wait_ready(20)


def test_telegram_runs_on_the_same_engine(svc, env):
    _started(svc)
    assert not svc.start_telegram() and svc.status().telegram == "not_configured"
    os.environ.update({"TELEGRAM_BOT_TOKEN": "123:fake", "SPECTRUM_PROJECT_SECRET": "secret"})
    svc.telegram.cmd = [sys.executable, "-c", FAKE_BRIDGE]
    assert svc.start_telegram()
    user = wait_for(svc, lambda e: e["type"] == "telegram_user")
    assert user["id"] == "parent:123:fake"  # parent mode, settings passed in the environment
    command = wait_for(svc, lambda e: e["type"] == "command" and e["source"] == "telegram")
    assert command["text"] == "reset"
    deadline = time.monotonic() + 20
    got = []
    while time.monotonic() < deadline:
        got = [json.loads(line[4:]) for line in list(svc.telegram.log) if line.startswith("got ")]
        if {"type": "done", "id": "n2"} in got:
            break
        time.sleep(0.05)
    assert {"type": "reply", "id": "n1", "text": "Okay, fresh start."} in got
    assert any(m["type"] == "approval" and "_test_confirm" in m["reason"] for m in got)
    assert {"type": "notice", "text": "Approval received."} in got
    assert {"type": "reply", "id": "n2", "text": "Confirmed photo."} in got
    assert {"type": "done", "id": "n2"} in got
    assert svc.status().telegram == "running"
    svc.stop_telegram()
    assert svc.status().telegram == "off"


def test_engine_command_from_source_and_installed(monkeypatch, tmp_path):
    assert engine_command() == [sys.executable, "-u", "-m", "jarvis.engine"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "Alfred.exe"))
    assert engine_command() == [str(tmp_path.resolve() / "alfred-cli.exe"), "--engine"]
    assert config.app_dir() == tmp_path.resolve()


def test_fake_service_has_the_same_api():
    fake = FakeService(delay=0.01)
    fake.start()
    assert fake.wait_ready(5)
    assert fake.run_and_wait("hello", timeout=5).replies == ["(fake) You said: hello"]
    cid = fake.submit("send the report")
    asked = wait_for(fake, lambda e: e["type"] == "approval", timeout=5)
    assert asked["id"] == cid and fake.approve(asked["token"], True) and not fake.approve(asked["token"], True)
    wait_for(fake, lambda e: e["type"] == "done" and e["id"] == cid, timeout=5)
    cid = fake.submit("long task")
    assert fake.stop() and fake.stop()
    assert wait_for(fake, lambda e: e["type"] == "done" and e["id"] == cid, timeout=5)["stopped"]
    assert wait_for(fake, lambda e: e["type"] == "ready" and e["after_stop"], timeout=5)
    for name in ("start", "shutdown", "restart_engine", "submit", "run_and_wait", "reset_conversation", "stop",
                 "approve", "status", "events", "subscribe", "wait_ready", "start_telegram", "stop_telegram",
                 "telegram_discovery"):
        assert callable(getattr(FakeService, name)) and callable(getattr(AlfredService, name)), name
    fake.shutdown()
    assert FakeService(consent=False).start().engine == "needs_consent"


def test_status_hook_and_unchanged_console_output(capsys, monkeypatch):
    from jarvis.__main__ import Jarvis

    cfg = config.Config("", "m", "low", "base.en", False, False, 5)
    seen, replies = [], []
    hooked = Jarvis(cfg, on_status=lambda kind, text: seen.append((kind, text)), on_reply=replies.append)
    hooked._brain_status("  -> open_app {'name': 'paint'}")
    hooked._brain_status("On it.")
    hooked.status("thinking", "thinking...")
    assert seen == [("tool", "-> open_app {'name': 'paint'}"), ("text", "On it."), ("thinking", "thinking...")]
    plain = Jarvis(cfg, on_reply=replies.append)
    plain._brain_status("  -> open_app {}")
    plain.status("thinking", "thinking...")
    assert capsys.readouterr().out == "    -> open_app {}\n  thinking...\n"  # as the console printed before
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    plain.handle("write a poem about my week")
    assert replies[-1] == "I only know simple commands without an API key. Add your Anthropic API key in Alfred settings."
