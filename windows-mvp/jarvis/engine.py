"""The Alfred engine: one process that runs commands for every front end (the app, Telegram, voice).

Protocol v2: one JSON object per line on stdin and stdout; stderr is the log.
  in   command{id,text,source}  approval{token,approved,by}  reset  ping{n}   (EOF on stdin: exit)
  out  ready{after_stop,released,version,has_llm,computer_use,name,protocol}  accepted{id}
       status{id,kind,text}  reply{id,source,text}  announce{text[,source]}  notice{text}
       approval{token,reason,timeout,id,source}  approval_closed{token,approved,by}  done{id,ok}  pong{n}
Commands run one at a time on the main thread; a reader thread takes approvals, pings and EOF
while one runs. An approval with source "remote" is another PC asking to act on this one; only
someone at this PC (the app) should answer it.
jarvis.service.AlfredService starts it; telegram_worker.py speaks the older v1.
"""

import json
import os
import queue
import re
import secrets
import subprocess
import sys
import threading
import time
import traceback

from . import __version__, config, consent, remote, tools
from .tools import registry
from .tools.registry import audit

PROTOCOL = 2
QUEUE_SIZE = 20
LOCAL_SOURCES = ("desktop", "voice", "console")  # replies to these, and reminders they set, are spoken on this PC
VOICED = ("voice",)  # but not replies to the app's voice mode: its live voice says them (app/voice.py)
EXPIRED = "That approval expired or is no longer pending."


class Approvals:
    """Approval requests waiting for an answer, each bound to a one-time token. The first answer wins."""

    def __init__(self, emit, timeout=60, on_close=None):
        self.emit = emit
        self.timeout = timeout
        self.on_close = on_close  # on_close(token, approved, by) once a request is answered or given up
        self.pending = {}
        self.lock = threading.Lock()

    def request(self, reason, **context):
        token = secrets.token_hex(4)
        event = threading.Event()
        entry = {"event": event, "approved": False}
        with self.lock:
            self.pending[token] = entry
        try:
            self.emit({"type": "approval", "token": token, "reason": reason, "timeout": self.timeout, **context})
            if not event.wait(self.timeout):
                self.resolve(token, False, by="timeout")
            return entry["approved"]
        finally:
            with self.lock:
                self.pending.pop(token, None)

    def resolve(self, token, approved, by=None):
        with self.lock:
            entry = self.pending.get(token)
            if entry is None or entry["event"].is_set():
                return False
            entry["approved"] = approved is True
            entry["event"].set()
        if self.on_close:
            self.on_close(token, approved is True, by)
        return True

    def cancel_all(self, by="shutdown"):
        with self.lock:
            closed = [token for token, entry in self.pending.items() if not entry["event"].is_set()]
            for token in closed:
                self.pending[token]["approved"] = False
                self.pending[token]["event"].set()
        if self.on_close:
            for token in closed:
                self.on_close(token, False, by)


def release_held_input() -> list[str]:
    """After a hard stop killed the previous engine mid-action, let go of anything it was holding."""
    if sys.platform != "win32":
        return []
    from .tools.wininput import enable_dpi_awareness, release_all

    enable_dpi_awareness()  # same as the console app, so computer-use clicks line up on scaled displays
    try:
        return release_all()
    except Exception:  # noqa: BLE001 - never block startup on this
        return []


def clean_source(source, default="desktop") -> str:
    """Front-end names end up in audit names ("telegram_stop"), so keep them short and plain."""
    source = re.sub(r"[^a-z0-9_-]+", "", str(source or "").lower())[:20]
    return source or default


class Engine:
    """Protocol v2. Subclasses (telegram_worker.LegacyWorker) change what goes out, not how commands run."""

    default_stop_source = "desktop"
    serve = True  # share this PC with the others when JARVIS_SERVE=1

    def __init__(self, out):
        self.out = out
        self.output_lock = threading.Lock()
        self.commands = queue.Queue(maxsize=QUEUE_SIZE)
        self.stopped = threading.Event()
        self.current = None  # {"id", "source"} of the command running now
        self.approvals = Approvals(self.emit, on_close=self.approval_closed)
        self.jarvis = None
        self.speaker = None
        self.cfg = None
        self.test_tools = os.environ.get("JARVIS_TEST_TOOLS") == "1"

    # --- output -----------------------------------------------------------
    def emit(self, message: dict) -> None:
        try:
            with self.output_lock:
                self.out.write(json.dumps(message, ensure_ascii=False) + "\n")
                self.out.flush()
        except (OSError, ValueError):
            self.stopped.set()  # whoever started us is gone

    def ready(self, after_stop: bool, released: list) -> None:
        cfg = self.cfg
        self.emit({"type": "ready", "after_stop": after_stop, "released": len(released), "version": __version__,
                   "has_llm": cfg.has_llm, "computer_use": cfg.computer_use, "name": cfg.name, "protocol": PROTOCOL})

    def notice(self, text: str) -> None:
        self.emit({"type": "notice", "text": text})

    def accepted(self, message: dict) -> None:
        self.emit({"type": "accepted", "id": message.get("id")})

    def rejected(self, message: dict) -> None:
        self.notice("Too many queued commands. Try again shortly.")
        if message.get("type") == "command":
            self.emit({"type": "done", "id": message.get("id"), "ok": False})

    def status(self, kind: str, text: str) -> None:
        current = self.current or {}
        self.emit({"type": "status", "id": current.get("id"), "kind": kind, "text": text})

    def reply(self, text) -> None:
        current = self.current or {}
        source = current.get("source")
        self.emit({"type": "reply", "id": current.get("id"), "source": source, "text": str(text)})
        if source in LOCAL_SOURCES and source not in VOICED and self.speaker and self.speaker.enabled:
            self.speaker.say(str(text))

    def announce(self, text, source=None) -> None:
        """Timers and messages from other PCs. source: the front end whose command set it, if known."""
        message = {"type": "announce", "text": str(text)}
        if source:
            message["source"] = source
        self.emit(message)
        if source in (None, *LOCAL_SOURCES) and self.speaker and self.speaker.enabled:
            self.speaker.say(str(text))

    def approval_context(self) -> dict:
        current = self.current or {}
        return {"id": current.get("id"), "source": current.get("source")}

    def approval_closed(self, token, approved, by) -> None:
        self.emit({"type": "approval_closed", "token": token, "approved": approved, "by": by})

    def approval_answered(self, resolved: bool) -> None:
        if not resolved:
            self.notice(EXPIRED)

    def done(self, message: dict, ok: bool) -> None:
        self.emit({"type": "done", "id": message.get("id"), "ok": ok})

    def failed(self, exc: Exception) -> str:
        return f"Command failed ({type(exc).__name__}). Alfred's log on the PC has the details."

    def remote_log(self, text: str) -> None:
        if self.cfg.token and self.cfg.token in text:
            return  # pairing lines carry the token; it never leaves through the protocol or logs
        self.notice(text.strip())

    # --- input ------------------------------------------------------------
    def confirm(self, reason: str) -> bool:
        return self.approvals.request(reason, **self.approval_context())

    def confirm_for_pc(self, reason: str) -> bool:
        """Another PC wants to do something consequential here: ask in the app, not where a command came from."""
        return self.approvals.request(reason, id=None, source="remote")

    def read_input(self, stream) -> None:
        try:
            for line in stream:
                if not line.strip():
                    continue
                try:
                    message = json.loads(line)
                    if isinstance(message, dict):
                        self.receive(message)
                except (ValueError, TypeError):
                    self.notice("Invalid bridge message.")
        finally:
            self.stopped.set()
            self.approvals.cancel_all()

    def receive(self, message: dict) -> None:
        kind = message.get("type")
        if kind == "approval":
            by = clean_source(message.get("by"), self.default_stop_source)
            self.approval_answered(self.approvals.resolve(message.get("token"), message.get("approved"), by))
        elif kind == "command" and isinstance(message.get("text"), str):
            self.enqueue(message)
        elif kind == "reset":
            self.enqueue({"type": "reset"})
        elif kind == "ping":
            self.emit({"type": "pong", "n": message.get("n")})

    def enqueue(self, message: dict) -> None:
        # Only this thread adds to the queue, so a check that it isn't full still holds for the put.
        if self.commands.full():
            self.rejected(message)
            return
        if message.get("type") == "command":
            self.accepted(message)
        self.commands.put_nowait(message)

    # --- running ----------------------------------------------------------
    def run_command(self, message: dict) -> None:
        source = clean_source(message.get("source"), self.default_stop_source)
        self.current = {"id": message.get("id"), "source": source}
        registry.reply_to(lambda text: self.announce(text, source))  # a timer set now reports back to this source
        ok = True
        try:
            if not self.run_test_tool(message["text"]):
                self.jarvis.handle(message["text"])
        except Exception as exc:  # noqa: BLE001 - one bad command must not take the engine down
            ok = False
            traceback.print_exc()  # full trace in the local log only
            self.reply(self.failed(exc))
        finally:
            registry.reply_to(None)
            self.current = None
            self.done(message, ok)

    def reset(self) -> None:
        if self.jarvis.brain:
            self.jarvis.brain.reset()
        self.notice("Okay, fresh start.")

    def run_test_tool(self, text: str) -> bool:
        """With JARVIS_TEST_TOOLS=1: "_test_confirm what" or "_test_sleep 30 [child]" call the hidden tools."""
        m = re.fullmatch(r"(_test_\w+)\s*(.*)", text.strip())
        if not (self.test_tools and m and m[1] in tools.TOOLS):
            return False
        name, words = m[1], m[2].split()
        if name == "_test_sleep":
            args = {"seconds": float(words[0])} if words else {}
            if "child" in words[1:]:
                args["child"] = True
        else:
            args = {"what": " ".join(words)} if words else {}
        self.jarvis.status("tool", f"-> {name} {args}")
        _, out = tools.run(name, args, self.jarvis.confirm)
        self.jarvis.reply(str(out))
        return True

    def start_remote(self) -> None:
        """Let Alfred reach this user's other PCs (and, with JARVIS_SERVE=1, be reached by them)."""
        cfg = self.cfg
        if self.serve and cfg.serve:
            remote.set_local_confirm(self.confirm_for_pc)
            try:
                remote.serve(cfg, log_fn=self.remote_log, background=True)
            except (OSError, ValueError) as e:
                self.remote_log(f"Couldn't share this PC with your other PCs. {e}")
        remote.connect(cfg, log_fn=self.remote_log)

    def run(self, stdin=None) -> int:
        from .__main__ import Jarvis
        from .voice import Speaker

        after_stop = os.environ.get("JARVIS_RESTART_REASON") == "stop"
        released = release_held_input()
        if after_stop:
            source = clean_source(os.environ.get("JARVIS_STOP_SOURCE"), self.default_stop_source)
            name = "Telegram" if source == "telegram" else source
            audit(f"{source}_stop", {}, True, f"hard stop from {name}; released: {', '.join(released) or 'nothing held'}")

        if not consent.accepted():
            # Only the person at the PC can accept the data notice.
            self.notice(consent.NOT_ACCEPTED)
            return 3

        self.cfg = cfg = config.load()
        self.speaker = speaker = Speaker(enabled=cfg.speak)
        registry.speak = lambda text: speaker.say(text) if speaker.enabled else print(f"(says) {text}")
        self.start_remote()
        self.jarvis = Jarvis(cfg, on_reply=self.reply, on_confirm=self.confirm, on_status=self.status)
        registry.announce = self.announce  # Jarvis points it at reply(); announcements have their own message
        if self.test_tools:
            register_test_tools()
        self.ready(after_stop, released)
        threading.Thread(target=self.read_input, args=(stdin or sys.stdin,), daemon=True,
                         name="alfred-engine-input").start()
        while not self.stopped.is_set():
            try:
                message = self.commands.get(timeout=0.2)
            except queue.Empty:
                continue
            if message.get("type") == "reset":
                self.reset()
            else:
                self.run_command(message)
        return 0


def register_test_tools() -> None:
    """Hidden tools for tests/test_service.py. Registered only with JARVIS_TEST_TOOLS=1."""
    registry.tool("_test_confirm", "Test only: does nothing, after the user approves.",
                  {"what": {"type": "string"}}, risk="confirm", hidden=True)(lambda what="it": f"Confirmed {what}.")
    registry.tool("_test_sleep", "Test only: waits.", {"seconds": {"type": "number"}, "child": {"type": "boolean"}},
                  hidden=True)(_test_sleep)


def _test_sleep(seconds: float = 30, child: bool = False) -> str:
    if child:  # a grandchild process, so tests can check that a hard stop kills the whole tree
        proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        registry.announcer()(f"child {proc.pid}")
    time.sleep(float(seconds))
    return "Slept."


def protocol_streams():
    """Keep the real stdout for the protocol; anything else that prints goes to the log (stderr)."""
    out = sys.stdout
    for stream in (sys.stdin, out):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass
    sys.stdout = sys.stderr
    return out


def main(engine_class=Engine) -> int:
    return engine_class(protocol_streams()).run()


if __name__ == "__main__":
    sys.exit(main())
