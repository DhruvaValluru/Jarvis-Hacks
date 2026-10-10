"""Telegram for the Alfred app: the Node bridge (telegram/index.mjs) in parent mode.

With ALFRED_BRIDGE_MODE=parent the bridge reads no .env and starts no Python. It sends us JSON lines:
  command{id,text}  approval{token,approved}  stop  telegram_user{id}  status{state}
and we answer with what Telegram should see, in the bridge's own worker format:
  reply{id,text}  notice{text}  approval{token,reason,timeout}  done{id}  ready{after_stop}
Commands run on the app's one engine with source "telegram"; only their replies and approval
requests go back to Telegram. jarvis-telegram.bat (legacy mode) is unchanged.
"""

import json
import os
import threading
import uuid
from collections import deque

from .. import config
from .process import Child

KEYS = (
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID", "SPECTRUM_PROJECT_ID", "SPECTRUM_PROJECT_SECRET",
    "OPENAI_API_KEY", "OPENAI_TTS_VOICE", "OPENAI_TTS_MODEL", "OPENAI_TRANSCRIBE_MODEL",
    "VOICE_SERVICE_URL", "VOICE_SERVICE_TOKEN",
)
REQUIRED = ("TELEGRAM_BOT_TOKEN", "SPECTRUM_PROJECT_SECRET")
DEFAULTS = {"SPECTRUM_PROJECT_ID": "908385d3-2f06-4c90-92d3-5937397eea69"}  # public, as in .env.example
STATES = ("off", "not_configured", "starting", "discovery", "running", "error")
EXPIRED = "That approval expired or is no longer pending."
_MISSING = object()


def bridge_command() -> list[str]:
    """node telegram/index.mjs; the installed app may ship its own node.exe next to it."""
    base = config.app_dir()
    node = base / "node" / "node.exe"
    return [str(node) if node.is_file() else "node", str(base / "telegram" / "index.mjs")]


class TelegramController:
    def __init__(self, service, cmd=None):
        self.service = service
        self.cmd = list(cmd) if cmd else None
        self.state = "off"
        self.last_error = ""
        self._lock = threading.Lock()
        self._child: Child | None = None
        self._ids: dict[str, object] = {}  # engine command id -> the bridge's id for it
        self._stop_source = ""
        self.log: deque[str] = deque(maxlen=100)

    def settings(self) -> dict[str, str]:
        return {**DEFAULTS, **config.child_env(KEYS, self.service.store)}

    def configured(self) -> bool:
        values = self.settings()
        return all(values.get(key) for key in REQUIRED)

    def start(self) -> bool:
        """Start the bridge if Telegram is set up (bot token and Spectrum secret saved)."""
        with self._lock:
            if self._child is not None:
                return True
            values = self.settings()
            if not all(values.get(key) for key in REQUIRED):
                self.state, self.last_error = "not_configured", ""
                child = None
            else:
                cmd = self.cmd or bridge_command()
                env = {**os.environ, **values, "ALFRED_BRIDGE_MODE": "parent",
                       "ALFRED_STATE_DIR": str(config.data_dir())}
                try:
                    child = Child(cmd, cwd=str(config.app_dir() / "telegram"), env=env)
                except OSError as e:
                    self.state, self.last_error = "error", f"Couldn't start the Telegram bridge: {e}"
                    child = None
                else:
                    self._child, self.state, self.last_error = child, "starting", ""
                    self._ids.clear()
        self._publish()
        if child is None:
            return False
        threading.Thread(target=self._read_out, args=(child,), daemon=True, name="alfred-telegram-out").start()
        threading.Thread(target=self._read_err, args=(child,), daemon=True, name="alfred-telegram-err").start()
        return True

    def stop(self, timeout: float = 5) -> None:
        with self._lock:
            child, self._child = self._child, None
            if child is None:
                return
            self.state = "off"
        child.close_stdin()  # the bridge exits when its input closes
        if child.wait(timeout) is None:
            child.kill_tree()
            child.wait(5)
        child.close()
        self._publish()

    # --- from the engine (called by AlfredService for each engine event) ----
    def on_stop(self, source: str) -> None:
        self._stop_source = source

    def on_engine(self, event: dict) -> None:
        if self._child is None:
            return
        kind, source = event.get("type"), event.get("source")
        if kind == "reply" and source == "telegram":
            self._send({"type": "reply", "id": self._ids.get(event.get("id")), "text": event.get("text", "")})
        elif kind == "announce" and source == "telegram":
            self._send({"type": "reply", "text": event.get("text", "")})
        elif kind == "approval" and source == "telegram":
            self._send({"type": "approval", "token": event.get("token"), "reason": event.get("reason"),
                        "timeout": event.get("timeout")})
        elif kind == "done":
            with self._lock:
                bridge_id = self._ids.pop(event.get("id"), _MISSING)
            if bridge_id is _MISSING:
                return
            if event.get("stopped") and self._stop_source != "telegram":
                self._send({"type": "notice", "text": "Alfred was stopped on the PC."})
            self._send({"type": "done", "id": bridge_id})
        elif kind == "ready" and event.get("after_stop") and self._stop_source == "telegram":
            self._stop_source = ""
            self._send({"type": "ready", "after_stop": True})

    # --- from the bridge ----------------------------------------------------
    def _on_bridge(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "command" and isinstance(message.get("text"), str):
            bridge_id, cid = message.get("id"), uuid.uuid4().hex[:12]
            with self._lock:
                self._ids[cid] = bridge_id
            try:
                self.service._submit(message["text"], "telegram", cid=cid)
            except (RuntimeError, ValueError) as e:  # NotReady, QueueFull, a bad command
                with self._lock:
                    self._ids.pop(cid, None)
                self._send({"type": "notice", "text": str(e)})
                self._send({"type": "done", "id": bridge_id})
        elif kind == "approval":
            ok = self.service.approve(str(message.get("token", "")), message.get("approved") is True, by="telegram")
            self._send({"type": "notice", "text": "Approval received." if ok else EXPIRED})
        elif kind == "stop":
            if not self.service.stop("telegram"):
                self._send({"type": "notice", "text": "Alfred isn't running on the PC right now."})
        elif kind == "telegram_user":
            self.service.bus.publish("telegram_user", id=str(message.get("id", "")))
        elif kind == "status" and message.get("state") in ("running", "discovery"):
            with self._lock:
                if self._child is not None:
                    self.state = message["state"]
            self._publish()

    def _send(self, message: dict) -> None:
        child = self._child
        if child is None:
            return
        try:
            child.send_line(json.dumps(message, ensure_ascii=False))
        except (OSError, ValueError):
            pass

    def _read_out(self, child: Child) -> None:
        try:
            for line in child.stdout:
                try:
                    message = json.loads(line)
                except ValueError:
                    if line.strip():
                        self.log.append(line.rstrip()[:500])
                    continue
                if isinstance(message, dict):
                    try:
                        self._on_bridge(message)
                    except Exception as e:  # noqa: BLE001 - keep reading
                        self.log.append(f"service error: {type(e).__name__}: {e}")
        except (OSError, ValueError):
            pass
        code = child.wait()
        with self._lock:
            if self._child is not child:
                return  # stop() ended it
            self._child = None
            last = next((line for line in reversed(self.log) if line.strip()), "")
            self.state = "error"
            self.last_error = f"The Telegram bridge stopped (exit code {code})" + (f": {last}" if last else ".")
        child.close()
        self._publish()

    def _read_err(self, child: Child) -> None:
        try:
            for line in child.stderr:
                if line.strip():
                    self.log.append(line.rstrip()[:500])
        except (OSError, ValueError):
            pass

    def _publish(self) -> None:
        self.service.bus.publish("telegram", state=self.state, error=self.last_error)
