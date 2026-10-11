"""FakeService: AlfredService's public API with scripted events and no processes, for UI work anywhere.

Commands it understands (anything else gets "(fake) You said: ..."):
  "confirm ..." / "send ..."   asks for approval first, then replies Done or Cancelled
  "sleep" / "long ..."         stays busy until stop() (or 30 s)
  "fail"                       finishes with ok=False
  "timer"                      replies now and announces a reminder 2 s later
"""

import threading
import time
import uuid

from .. import consent as terms
from ..engine import clean_source
from .core import CommandResult, NotReady, QueueFull, Status
from .events import EventBus


class FakeService:
    def __init__(self, store=None, *, engine_cmd=None, bridge_cmd=None, bus=None, delay=0.3, consent=True):
        self.store = store
        self.bus = bus or EventBus()
        self.delay = delay  # seconds each scripted step takes
        self.consent = consent  # None: like the real service, ask consent.accepted()
        self._lock = threading.RLock()
        self._state = "off"
        self._telegram = "off"
        self._discovery = False
        self._commands: dict[str, dict] = {}
        self._approvals: dict[str, dict] = {}
        self._answers: dict[str, threading.Event] = {}
        self._stop = threading.Event()
        self._run_lock = threading.Lock()  # one command at a time, like the engine
        self._gen = 0
        self._shown = None

    # lifecycle
    def start(self) -> Status:
        with self._lock:
            if self._state not in ("off", "needs_consent", "crashed"):
                return self.status()
            if not self._consented():
                self._set("needs_consent")
                return self.status()
            self._set("starting")
        self._later(self.delay, self._ready, False)
        return self.status()

    def shutdown(self, timeout: float = 5) -> None:
        self._stop.set()
        with self._lock:
            self._gen += 1
            self._fail_all(stopped=False)
            self._telegram = "off"
            self._set("off")

    def restart_engine(self, reason: str = "restart") -> None:
        with self._lock:
            self._gen += 1
            self._fail_all(stopped=False)
            if not self._consented():
                self._set("needs_consent")
                return
            self._set("starting")
        self._later(self.delay, self._ready, False)

    def _consented(self) -> bool:
        return terms.accepted() if self.consent is None else bool(self.consent)

    def stop(self, source: str = "desktop") -> bool:
        with self._lock:
            if self._state in ("off", "needs_consent", "crashed"):
                return False
            if self._state == "stopping":
                return True
            self._gen += 1
            self._stop.set()
            self._set("stopping", publish=False)
        self.bus.publish("stop", source=clean_source(source))
        self._publish_state()
        self._later(self.delay, self._after_stop)
        return True

    def wait_ready(self, timeout: float = 30) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            state = self.status().engine
            if state in ("ready", "busy"):
                return True
            if state in ("off", "needs_consent", "crashed"):
                return False
            time.sleep(0.02)
        return False

    # commands
    def submit(self, text: str, source: str = "desktop") -> str:
        return self._submit(text, source)["id"]

    def _submit(self, text: str, source: str) -> dict:
        text, source = str(text or "").strip(), clean_source(source)
        if not text:
            raise ValueError("The command is empty.")
        with self._lock:
            if self._state not in ("ready", "busy"):
                raise NotReady("Alfred is restarting. Send that again in a few seconds.")
            if len(self._commands) >= 20:
                raise QueueFull("The command queue is full. Wait for a reply before sending more.")
            cid = uuid.uuid4().hex[:12]
            cmd = self._commands[cid] = {"id": cid, "text": text, "source": source, "replies": [],
                                         "done": threading.Event(), "ok": False, "stopped": False}
            gen = self._gen
        self.bus.publish("command", id=cid, source=source, text=text)
        self._publish_state()
        self.bus.publish("accepted", id=cid)
        threading.Thread(target=self._script, args=(cmd, gen), daemon=True).start()
        return cmd

    def run_and_wait(self, text: str, source: str = "desktop", timeout: float = 120) -> CommandResult:
        cmd = self._submit(text, source)
        finished = cmd["done"].wait(timeout)
        return CommandResult(cmd["id"], cmd["ok"] and finished, list(cmd["replies"]), cmd["stopped"], not finished)

    def reset_conversation(self) -> bool:
        if self.status().engine not in ("ready", "busy"):
            return False
        self.bus.publish("notice", text="Okay, fresh start.")
        return True

    def approve(self, token: str, approved: bool, by: str = "desktop") -> bool:
        with self._lock:
            entry = self._approvals.pop(token, None)
            if entry is None:
                return False
            entry["approved"] = approved is True
        self.bus.publish("approval_closed", token=token, approved=approved is True, by=clean_source(by))
        self._answers.pop(token, threading.Event()).set()
        return True

    def start_telegram(self) -> bool:
        self._telegram = "discovery" if self._discovery else "running"
        self.bus.publish("telegram", state=self._telegram, error="")
        return True

    def stop_telegram(self) -> None:
        self._telegram = "off"
        self.bus.publish("telegram", state="off", error="")

    def telegram_discovery(self, on: bool) -> None:
        self._discovery = bool(on)

    # reading
    def status(self) -> Status:
        with self._lock:
            ids = list(self._commands)
            busy = self._state == "ready" and bool(ids)
            return Status(engine="busy" if busy else self._state, busy_id=ids[0] if busy else None,
                          queued=max(0, len(ids) - 1) if busy else 0, has_llm=True, model="claude-opus-5-5",
                          computer_use=True, pc_name="fake-pc", telegram=self._telegram,
                          approvals=[{k: v for k, v in a.items() if k != "approved"} for a in self._approvals.values()])

    def events(self, after: int | None = None, timeout: float = 25) -> list[dict]:
        return self.bus.events(after, timeout)

    def subscribe(self, fn):
        return self.bus.subscribe(fn)

    # script
    def _script(self, cmd: dict, gen: int) -> None:
        with self._run_lock:
            if gen == self._gen:
                self._run(cmd, gen)

    def _run(self, cmd: dict, gen: int) -> None:
        cid, text = cmd["id"], cmd["text"].lower()
        self._sleep(self.delay)
        self._emit(gen, "status", id=cid, kind="thinking", text="thinking...")
        ok = True
        if text.startswith(("confirm", "send")):
            self._emit(gen, "status", id=cid, kind="tool", text="-> ui_click {'name': 'Send'}")
            token = uuid.uuid4().hex[:8]
            answered = self._answers[token] = threading.Event()
            entry = {"token": token, "reason": f"click Send ({cmd['text']})", "timeout": 60,
                     "id": cid, "source": cmd["source"], "approved": False}
            with self._lock:
                self._approvals[token] = entry
            self._emit(gen, "approval", **{k: v for k, v in entry.items() if k != "approved"})
            self._publish_state()
            answered.wait(60)
            self._reply(gen, cid, "Done, sent it." if entry["approved"] else "Okay, I didn't send it.")
        elif text.startswith(("sleep", "long")):
            self._emit(gen, "status", id=cid, kind="tool", text="-> computer left_click [640, 400]")
            self._sleep(30)
            self._reply(gen, cid, "Finished the long task.")
        elif text == "fail":
            ok = False
            self._reply(gen, cid, "Command failed (FakeError). Alfred's log on the PC has the details.")
        elif text.startswith("timer"):
            self._reply(gen, cid, "Timer set for 2 s.")
            self._later(2, lambda: self._emit(gen, "announce", text="Reminder: Time's up.", source=cmd["source"]))
        else:
            self._sleep(self.delay)
            self._reply(gen, cid, f"(fake) You said: {cmd['text']}")
        self._finish(gen, cid, ok)

    def _reply(self, gen, cid, text) -> None:
        cmd = self._commands.get(cid)
        if cmd and gen == self._gen:
            cmd["replies"].append(text)
            self._emit(gen, "reply", id=cid, source=cmd["source"], text=text)

    def _finish(self, gen, cid, ok) -> None:
        with self._lock:
            if gen != self._gen:
                return
            cmd = self._commands.pop(cid, None)
        if cmd:
            cmd["ok"] = ok
            self.bus.publish("done", id=cid, ok=ok, source=cmd["source"], stopped=False)
            cmd["done"].set()
            self._publish_state()

    def _emit(self, gen, event_type, **fields) -> None:
        if gen == self._gen:
            self.bus.publish(event_type, **fields)

    def _ready(self, after_stop: bool) -> None:
        with self._lock:
            if self._state != "starting":
                return
            self._stop.clear()
            self._set("ready", publish=False)
        self.bus.publish("ready", after_stop=after_stop, released=0, version="fake", has_llm=True,
                         computer_use=True, name="fake-pc", protocol=2)
        self._publish_state()

    def _after_stop(self) -> None:
        with self._lock:
            self._fail_all(stopped=True)
            self._set("starting")
        self._later(self.delay, self._ready, True)

    def _fail_all(self, stopped: bool) -> None:
        for token in list(self._approvals):
            self._approvals.pop(token)
            self.bus.publish("approval_closed", token=token, approved=False, by="stop" if stopped else "exit")
            self._answers.pop(token, threading.Event()).set()
        for cid, cmd in list(self._commands.items()):
            self._commands.pop(cid)
            cmd["stopped"] = stopped
            self.bus.publish("done", id=cid, ok=False, source=cmd["source"], stopped=stopped)
            cmd["done"].set()

    def _set(self, state: str, publish: bool = True) -> None:
        self._state = state
        if publish:
            self._publish_state()

    def _publish_state(self) -> None:
        with self._lock:
            st = self.status()
            key = (st.engine, st.busy_id, st.queued)
            if key == self._shown:
                return
            self._shown = key
        self.bus.publish("state", state=st.engine, busy_id=st.busy_id, queued=st.queued)

    def _sleep(self, seconds: float) -> None:
        self._stop.wait(seconds)

    def _later(self, seconds: float, fn, *args) -> None:
        timer = threading.Timer(seconds, fn, args)
        timer.daemon = True
        timer.start()
