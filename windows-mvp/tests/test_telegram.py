"""Telegram worker tests do not call the network or control desktop apps."""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.config import Config
from jarvis.__main__ import Jarvis
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
           "LOCALAPPDATA": str(tmp_path), "PYTHONIOENCODING": "utf-8"}
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
