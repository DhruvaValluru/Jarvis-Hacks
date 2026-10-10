"""Telegram worker tests do not call the network or control desktop apps."""

import json
import os
import subprocess
import sys
import threading
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.config import Config
from jarvis.__main__ import Jarvis
from jarvis import consent
from jarvis.telegram_worker import Approvals


def test_remote_reply_and_confirmation_bypass_console():
    replies, reasons = [], []
    jarvis = Jarvis(Config("", "unused", "low", "base.en", False, False, 5),
                    on_reply=replies.append,
                    on_confirm=lambda reason: reasons.append(reason) or True)
    jarvis.reply("hello")
    assert jarvis.confirm("send message")
    assert replies == ["hello"] and reasons == ["send message"]


def test_approval_is_bound_to_token_and_consumed_once():
    notices = []
    ready = threading.Event()
    approvals = Approvals(lambda m: (notices.append(m), ready.set()), timeout=1)
    result = []
    worker = threading.Thread(target=lambda: result.append(approvals.request("send")))
    worker.start()
    assert ready.wait(1)
    assert not approvals.resolve("wrong", True)
    token = notices[0]["token"]
    assert approvals.resolve(token, True)
    assert not approvals.resolve(token, True)
    worker.join(2)
    assert result == [True]
    assert not approvals.resolve(token, True)


def test_approval_timeout_and_shutdown_decline():
    approvals = Approvals(lambda _: None, timeout=0.01)
    assert not approvals.request("send")
    ready = threading.Event()
    approvals = Approvals(lambda _: ready.set(), timeout=10)
    result = []
    worker = threading.Thread(target=lambda: result.append(approvals.request("send")))
    worker.start()
    assert ready.wait(1)
    approvals.cancel_all()
    worker.join(1)
    assert result == [False]


def test_worker_protocol_runs_command_without_console_or_api(tmp_path):
    env = {**os.environ, "ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": "",
           "LOCALAPPDATA": str(tmp_path), "PYTHONIOENCODING": "utf-8",
           "JARVIS_ACCEPT_TERMS": consent.TERMS_VERSION}
    process = subprocess.Popen([sys.executable, "-u", "-m", "jarvis.telegram_worker"],
                               cwd=Path(__file__).resolve().parent.parent, env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    messages = []
    # A reader thread avoids a hanging test if the bridge stops emitting output.
    finished = threading.Event()
    def read():
        for line in process.stdout:
            messages.append(json.loads(line))
            if messages[-1].get("type") == "done":
                finished.set()
    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        process.stdin.write(json.dumps({"type": "command", "id": "test", "text": "reset"}) + "\n")
        process.stdin.flush()
        assert finished.wait(5), "worker did not finish"
        assert {"type": "reply", "text": "Okay, fresh start."} in messages
        assert {"type": "done", "id": "test"} in messages
        process.stdin.close()
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def _run_worker(tmp_path, **extra_env):
    env = {**os.environ, "ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": "",
           "LOCALAPPDATA": str(tmp_path), "PYTHONIOENCODING": "utf-8", **extra_env}
    if "JARVIS_ACCEPT_TERMS" not in extra_env:
        env.pop("JARVIS_ACCEPT_TERMS", None)  # test the real consent check, not a bypass from the caller
    process = subprocess.Popen([sys.executable, "-u", "-m", "jarvis.telegram_worker"],
                               cwd=Path(__file__).resolve().parent.parent, env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    return process


def test_worker_refuses_to_run_until_the_data_notice_is_accepted(tmp_path):
    process = _run_worker(tmp_path)
    out, _ = process.communicate(timeout=10)
    assert process.returncode == 3
    assert json.loads(out.splitlines()[0]) == {"type": "notice", "text": consent.NOT_ACCEPTED}


def test_worker_after_stop_reports_it_and_audits(tmp_path):
    process = _run_worker(tmp_path, JARVIS_ACCEPT_TERMS=consent.TERMS_VERSION, JARVIS_RESTART_REASON="stop")
    try:
        first = json.loads(process.stdout.readline())
        assert first["type"] == "ready" and first["after_stop"] is True
    finally:
        process.stdin.close()
        process.wait(timeout=5)
    log = (tmp_path / "Jarvis" / "audit.jsonl").read_text(encoding="utf-8")
    assert '"tool": "telegram_stop"' in log


def test_consent_is_versioned_and_stored_per_user(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.delenv("JARVIS_ACCEPT_TERMS", raising=False)
    assert not consent.accepted()
    assert not consent.ensure(interactive=False)
    monkeypatch.setattr("builtins.input", lambda _: "agree")
    assert consent.ensure(interactive=True)
    assert consent.accepted()
    (tmp_path / "Jarvis" / "consent.json").write_text('{"version": "old"}', encoding="utf-8")
    assert not consent.accepted()  # a new notice version must be accepted again
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert not consent.ensure(interactive=True)


def test_release_all_lets_go_of_only_what_is_held():
    from jarvis.tools.wininput import release_all

    held = {0x01, 0x10, 0x41}  # left mouse button, Shift, the A key
    calls = []
    fake = SimpleNamespace(
        user32=SimpleNamespace(GetAsyncKeyState=lambda vk: 0x8000 if vk in held else 0),
        button=lambda name, down: calls.append(("button", name, down)),
        key=lambda vk, down: calls.append(("key", vk, down)),
    )
    released = release_all(fake)
    assert calls == [("button", "left", False), ("key", 0x10, False), ("key", 0x41, False)]
    assert len(released) == 3
