"""AlfredService: what the Alfred app's window talks to, in the same process.

It runs the one engine process (engine.py), restarts it when it crashes, hard-stops it on Stop, and
turns its output into numbered events the UI reads with events() or subscribe(). Commands from
Telegram run on the same engine (bridge.py), so there is one conversation and one Stop.

Events (each also has seq, ts and type): the engine's own messages (accepted, status, reply,
announce, notice, approval, approval_closed, done{id,ok,source,stopped}, ready), plus
  state{state,busy_id,queued}   command{id,source,text}   stop{source}
  crash{code,error,restart_in}  (restart_in is null once it gives up: state crashed)
  telegram{state,error}         telegram_user{id,text}
"""

import json
import os
import sys
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass, field

from .. import config, consent
from ..engine import clean_source
from .bridge import TelegramController
from .events import EventBus
from .process import Child

STATES = ("off", "needs_consent", "starting", "ready", "busy", "stopping", "crashed")
MAX_QUEUED = 20  # the engine's own queue holds 20 besides the one running
MAX_TEXT = 10_000


class NotReady(RuntimeError):
    """The engine isn't taking commands right now (starting, stopping, off, crashed, needs consent)."""


class QueueFull(RuntimeError):
    """Too many commands are already waiting."""


@dataclass
class CommandResult:
    id: str
    ok: bool
    replies: list[str]
    stopped: bool = False
    timed_out: bool = False


@dataclass
class Status:
    engine: str  # one of STATES
    busy_id: str | None = None
    queued: int = 0
    has_llm: bool = False
    model: str = ""
    computer_use: bool = False
    pc_name: str = ""
    telegram: str = "off"
    approvals: list[dict] = field(default_factory=list)
    last_error: str = ""


def engine_command() -> list[str]:
    """How to start the engine: from source, this Python; installed, the bundled alfred-cli.exe."""
    if config.frozen():
        return [str(config.app_dir() / "alfred-cli.exe"), "--engine"]
    return [sys.executable, "-u", "-m", "jarvis.engine"]


@dataclass
class _Command:
    id: str
    text: str
    source: str
    replies: list = field(default_factory=list)
    finished: threading.Event = field(default_factory=threading.Event)
    ok: bool = False
    stopped: bool = False


class _Engine:
    """One engine process and what we know about it."""

    def __init__(self, child: Child):
        self.child = child
        self.ready = False
        self.intent = None  # why we are ending it: "stop", "restart" or "shutdown"; None = it crashed
        self.stop_source = ""
        self.reason = ""
        self.exited = False
        self.notice = ""
        self.log: deque[str] = deque(maxlen=50)
        self.err_reader: threading.Thread | None = None


class AlfredService:
    RESTART_DELAYS = (1.0, 2.0, 4.0)  # before each automatic restart after a crash
    CRASH_WINDOW = 60.0  # more than len(RESTART_DELAYS) crashes in this many seconds: give up

    def __init__(self, store=None, *, engine_cmd=None, bridge_cmd=None, bus=None):
        if store is None:
            from ..settings import Store

            store = Store()
        self.store = store
        self.engine_cmd = list(engine_cmd) if engine_cmd else engine_command()
        self.bus = bus or EventBus()
        self.telegram = TelegramController(self, cmd=bridge_cmd)
        self._lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._engine: _Engine | None = None
        self._base = "off"  # every state but busy (that's ready with commands outstanding)
        self._commands: OrderedDict[str, _Command] = OrderedDict()
        self._approvals: dict[str, dict] = {}
        self._info: dict = {}
        self._model = ""
        self._last_error = ""
        self._crashes: list[float] = []
        self._wanted = False  # start() was called and shutdown() wasn't
        self._shown = None

    # --- lifecycle --------------------------------------------------------
    def start(self) -> "Status":
        """Start the engine; no-op while it runs. Without consent the state is needs_consent and
        nothing starts: call start() again after consent.record()."""
        with self._lock:
            self._wanted = True
            if self._engine is None:
                if not consent.accepted():
                    self._base = "needs_consent"
                else:
                    self._crashes.clear()
                    self._spawn()
        self._publish_state()
        return self.status()

    def shutdown(self, timeout: float = 5) -> None:
        """Stop Telegram and the engine. The engine may finish its current command for up to
        `timeout` seconds; then it is killed."""
        self.telegram.stop(timeout)
        with self._lock:
            self._wanted = False
            eng = self._engine
            if eng is not None and eng.intent != "stop":
                eng.intent = "shutdown"
        if eng is not None:
            eng.child.close_stdin()
            code = eng.child.wait(timeout)
            if code is None:
                eng.child.kill_tree()
                code = eng.child.wait(5)
            self._on_exit(eng, code)
        with self._lock:
            self._base = "off"
        self._publish_state()

    def restart_engine(self, reason: str = "restart") -> None:
        """Restart the engine, e.g. after settings changed. It finishes the command it is running
        (up to 30 s) and queued commands are dropped. Returns at once."""
        reason = clean_source(reason, "restart")
        with self._lock:
            eng = self._engine
            if eng is None:  # not running (or crashed): just start
                self._wanted = True
                self._crashes.clear()
                if consent.accepted():
                    self._spawn(reason)
                else:
                    self._base = "needs_consent"
            elif eng.intent is None:
                eng.intent, eng.reason = "restart", reason
                self._base = "starting"
            else:
                return  # already stopping or restarting
        self._publish_state()
        if eng is not None:
            threading.Thread(target=self._end, args=(eng, 30), daemon=True, name="alfred-restart").start()

    def stop(self, source: str = "desktop") -> bool:
        """HARD stop: kill the engine and everything it started, then start a fresh one (which lets go
        of held keys and buttons and audits "<source>_stop"). Returns at once; calling it again while
        stopping does nothing. False if no engine is running."""
        source = clean_source(source)
        with self._lock:
            eng = self._engine
            if eng is None:
                return False
            if eng.intent == "stop":
                return True
            eng.intent, eng.stop_source = "stop", source
            self._base = "stopping"
        self.bus.publish("stop", source=source)
        self.telegram.on_stop(source)
        self._publish_state()
        threading.Thread(target=self._kill, args=(eng,), daemon=True, name="alfred-stop").start()
        return True

    def wait_ready(self, timeout: float = 30) -> bool:
        """Wait until the engine takes commands. False on timeout, or if it can't start
        (needs consent, crashed or off)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.status().engine
            if state in ("ready", "busy"):
                return True
            if state in ("needs_consent", "crashed", "off"):
                return False
            time.sleep(0.05)
        return False

    # --- commands ---------------------------------------------------------
    def submit(self, text: str, source: str = "desktop") -> str:
        """Queue a command; returns its id. Raises NotReady or QueueFull."""
        return self._submit(text, source).id

    def run_and_wait(self, text: str, source: str = "desktop", timeout: float = 120) -> CommandResult:
        cmd = self._submit(text, source)
        finished = cmd.finished.wait(timeout)
        return CommandResult(cmd.id, cmd.ok and finished, list(cmd.replies), cmd.stopped, not finished)

    def reset_conversation(self) -> bool:
        """Forget the conversation (after the current command). False if the engine isn't running."""
        with self._lock:
            eng = self._engine
            if eng is None or not eng.ready or eng.intent is not None:
                return False
        return self._send(eng, {"type": "reset"})

    def approve(self, token: str, approved: bool, by: str = "desktop") -> bool:
        """Answer an approval request. The first answer wins: False if it was already answered,
        expired or unknown."""
        with self._lock:
            eng = self._engine
            if eng is None or self._approvals.pop(token, None) is None:
                return False
        return self._send(eng, {"type": "approval", "token": token, "approved": approved is True,
                                "by": clean_source(by)})

    # --- telegram ---------------------------------------------------------
    def start_telegram(self) -> bool:
        """Start the Telegram bridge (parent mode) if it is set up. Returns whether it started."""
        return self.telegram.start()

    def stop_telegram(self) -> None:
        self.telegram.stop()

    def telegram_discovery(self, on: bool) -> None:
        """While the setup links an owner: the bridge starts without one (discovery mode) until this is
        turned off again. Nothing is saved; restart the bridge for it to take effect."""
        self.telegram.discovery = bool(on)

    # --- reading ----------------------------------------------------------
    def status(self) -> Status:
        with self._lock:
            ids = list(self._commands)
            busy = self._base == "ready" and bool(ids)
            info = self._info
            return Status(
                engine="busy" if busy else self._base,
                busy_id=ids[0] if busy else None,
                queued=max(0, len(ids) - 1) if busy else 0,
                has_llm=bool(info.get("has_llm")),
                model=self._model,
                computer_use=bool(info.get("computer_use")),
                pc_name=str(info.get("name") or ""),
                telegram=self.telegram.state,
                approvals=[dict(a) for a in self._approvals.values()],
                last_error=self._last_error,
            )

    def events(self, after: int | None = None, timeout: float = 25) -> list[dict]:
        return self.bus.events(after, timeout)

    def subscribe(self, fn):
        """fn(event) for every new event, on the thread that has it: keep fn quick and don't block."""
        return self.bus.subscribe(fn)

    # --- internals --------------------------------------------------------
    def _submit(self, text: str, source: str, cid: str | None = None) -> _Command:
        text = str(text or "").strip()
        if not text:
            raise ValueError("The command is empty.")
        if len(text) > MAX_TEXT:
            raise ValueError("That command is too long.")
        cmd = _Command(cid or uuid.uuid4().hex[:12], text, clean_source(source))
        with self._lock:
            eng = self._engine
            if eng is None or not eng.ready or eng.intent is not None:
                raise NotReady(self._not_ready_text())
            if len(self._commands) >= MAX_QUEUED:
                raise QueueFull("The command queue is full. Wait for a reply before sending more.")
            self._commands[cmd.id] = cmd
        self.bus.publish("command", id=cmd.id, source=cmd.source, text=text)
        self._publish_state()
        if not self._send(eng, {"type": "command", "id": cmd.id, "text": text, "source": cmd.source}):
            raise NotReady(self._not_ready_text())  # the engine just died; _on_exit reports the command
        return cmd

    def _not_ready_text(self) -> str:
        state = self.status().engine
        if state in ("starting", "stopping"):
            return "Alfred is restarting. Send that again in a few seconds."
        if state == "needs_consent":
            return "Alfred's data notice hasn't been accepted on this PC yet."
        if state == "crashed":
            return "Alfred stopped working on the PC. Restart it from the Alfred app."
        return "Alfred isn't running on the PC right now."

    def _send(self, eng: _Engine, message: dict) -> bool:
        try:
            eng.child.send_line(json.dumps(message, ensure_ascii=False))
            return True
        except (OSError, ValueError):
            return False

    def _spawn(self, reason: str = "", stop_source: str = "") -> None:
        """Start an engine process. Call with self._lock held."""
        try:
            self._model = config.load(self.store).model
        except Exception:  # noqa: BLE001 - the engine will report a bad setting itself
            self._model = ""
        env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
               "JARVIS_RESTART_REASON": reason, "JARVIS_STOP_SOURCE": stop_source}
        if sys.platform == "win32":
            env["JARVIS_ENGINE_JOB"] = "1"  # apps it opens for the user leave the job (tools/apps.py)
        if getattr(self.store, "path", None):
            env["JARVIS_SETTINGS_FILE"] = str(self.store.path)  # the engine reads the same settings.env
        child = None
        try:
            child = Child(self.engine_cmd, cwd=str(config.app_dir()), env=env)
            eng = _Engine(child)
            eng.err_reader = threading.Thread(target=self._read_err, args=(eng,), daemon=True,
                                              name="alfred-engine-err")
            reader = threading.Thread(target=self._read_out, args=(eng,), daemon=True, name="alfred-engine-out")
            eng.err_reader.start()
            reader.start()
        except Exception as e:  # noqa: BLE001 - no executable, no threads left, ...: report it, don't hang
            if child is not None:
                child.close()
            self._engine, self._base = None, "crashed"
            self._last_error = f"Couldn't start Alfred's engine: {e}"
            return
        self._engine, self._base, self._info = eng, "starting", {}

    def _read_out(self, eng: _Engine) -> None:
        try:
            for line in eng.child.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except ValueError:
                    eng.log.append(line[:500])
                    continue
                if isinstance(message, dict):
                    try:
                        self._on_message(eng, message)
                    except Exception as e:  # noqa: BLE001 - keep reading whatever happens
                        eng.log.append(f"service error: {type(e).__name__}: {e}")
        except (OSError, ValueError):
            pass
        self._on_exit(eng, eng.child.wait())

    def _read_err(self, eng: _Engine) -> None:
        try:
            for line in eng.child.stderr:
                if line.strip():
                    eng.log.append(line.rstrip()[:500])
        except (OSError, ValueError):
            pass

    def _on_message(self, eng: _Engine, message: dict) -> None:
        kind = message.get("type")
        fields = {k: v for k, v in message.items() if k != "type"}
        cmd = None  # the command this message finished
        with self._lock:
            if eng is not self._engine:
                return
            if kind == "ready":
                eng.ready = True
                self._info = {k: message.get(k) for k in ("has_llm", "computer_use", "name", "version")}
                if self._base == "starting":
                    self._base = "ready"
                self._last_error = ""
            elif kind == "reply":
                if (running := self._commands.get(message.get("id"))) is not None:
                    running.replies.append(str(message.get("text", "")))
            elif kind == "approval":
                token = str(message.get("token", ""))
                self._approvals[token] = {k: message.get(k) for k in ("token", "reason", "timeout", "id", "source")}
            elif kind == "approval_closed":
                self._approvals.pop(message.get("token"), None)
            elif kind == "done":
                cmd = self._commands.pop(message.get("id"), None)
                if cmd is not None:
                    cmd.ok = message.get("ok") is True
                    fields.update(source=cmd.source, stopped=False)
            elif kind == "notice":
                eng.notice = str(message.get("text", ""))
            elif kind == "pong":
                return
        self.bus.publish(kind, **fields)
        if cmd is not None:
            cmd.finished.set()
        self.telegram.on_engine({"type": kind, **fields})
        if kind in ("ready", "done", "approval", "approval_closed"):
            self._publish_state()

    def _kill(self, eng: _Engine) -> None:
        eng.child.kill_tree()
        self._on_exit(eng, eng.child.wait(10))

    def _end(self, eng: _Engine, timeout: float) -> None:
        """Let the engine finish (EOF on stdin), killing it if it takes longer than timeout."""
        eng.child.close_stdin()
        code = eng.child.wait(timeout)
        if code is None:
            eng.child.kill_tree()
            code = eng.child.wait(5)
        self._on_exit(eng, code)

    def _on_exit(self, eng: _Engine, code) -> None:
        """The engine process ended (any reason). Runs once per engine."""
        if eng.intent is None and eng.err_reader is not None:
            eng.err_reader.join(1)  # a crash: let its last words reach the log first
        crash = None
        with self._lock:
            if eng.exited:
                return
            eng.exited = True
            current = eng is self._engine
            if current:
                self._engine = None
            intent = eng.intent
            killed = list(self._commands.values()) if current else []
            closed = list(self._approvals) if current else []
            if current:
                self._commands.clear()
                self._approvals.clear()
            for cmd in killed:
                cmd.ok, cmd.stopped = False, intent == "stop"
            respawn_in, respawn = None, None
            if not current:
                pass
            elif not self._wanted or intent == "shutdown":
                self._base = "off"
            elif intent in ("stop", "restart"):
                respawn = ("stop", eng.stop_source) if intent == "stop" else (eng.reason, "")
                self._base = "starting"
            elif code == 3:
                self._base = "needs_consent"
                self._last_error = eng.notice or consent.NOT_ACCEPTED
            else:
                last = next((line for line in reversed(eng.log) if line.strip()), "")
                self._last_error = f"Alfred's engine stopped unexpectedly (exit code {code})" + (f": {last}" if last else ".")
                now = time.monotonic()
                self._crashes = [t for t in self._crashes if now - t < self.CRASH_WINDOW]
                if len(self._crashes) >= len(self.RESTART_DELAYS):
                    self._base = "crashed"
                else:
                    respawn_in = self.RESTART_DELAYS[len(self._crashes)]
                    self._crashes.append(now)
                    self._base = "starting"
                crash = {"code": code, "error": self._last_error, "restart_in": respawn_in}
        eng.child.close()
        if crash is not None:
            self.bus.publish("crash", **crash)
        for token in closed:
            self.bus.publish("approval_closed", token=token, approved=False, by=intent or "exit")
        for cmd in killed:
            fields = {"id": cmd.id, "ok": False, "source": cmd.source, "stopped": cmd.stopped}
            self.bus.publish("done", **fields)
            cmd.finished.set()
            self.telegram.on_engine({"type": "done", **fields})
        if respawn is not None:  # after the events above, so "done (stopped)" comes before the new "ready"
            self._respawn(*respawn)
        elif respawn_in is not None:
            timer = threading.Timer(respawn_in, self._respawn, ("crash",))
            timer.daemon = True
            timer.start()
        self._publish_state()

    def _respawn(self, reason: str, stop_source: str = "") -> None:
        with self._lock:
            if not self._wanted or self._engine is not None or self._base != "starting":
                return
            self._spawn(reason, stop_source)
        self._publish_state()

    def _publish_state(self) -> None:
        with self._state_lock:
            st = self.status()
            key = (st.engine, st.busy_id, st.queued)
            if key == self._shown:
                return
            self._shown = key
            self.bus.publish("state", state=st.engine, busy_id=st.busy_id, queued=st.queued)
