"""Local JSON-lines bridge. Only the parent Telegram process connects to it.

Commands run serially on the main Windows user-session thread. A separate
reader accepts approval replies while a command is waiting for confirmation.
"""

import contextlib
import json
import queue
import secrets
import sys
import threading

from . import config
from .__main__ import Jarvis


class Approvals:
    def __init__(self, emit, timeout=60):
        self.emit = emit
        self.timeout = timeout
        self.pending = {}
        self.lock = threading.Lock()

    def request(self, reason):
        token = secrets.token_hex(4)
        event = threading.Event()
        entry = {"event": event, "approved": False}
        with self.lock:
            self.pending[token] = entry
        try:
            self.emit({"type": "approval", "token": token, "reason": reason,
                       "timeout": self.timeout})
            event.wait(self.timeout)
            return entry["approved"]
        finally:
            with self.lock:
                self.pending.pop(token, None)

    def resolve(self, token, approved):
        with self.lock:
            entry = self.pending.get(token)
            if entry is None or entry["event"].is_set():
                return False
            entry["approved"] = approved is True
            entry["event"].set()
            return True

    def cancel_all(self):
        with self.lock:
            for entry in self.pending.values():
                entry["approved"] = False
                entry["event"].set()


def main():
    if sys.platform == "win32":
        from .tools.wininput import enable_dpi_awareness

        enable_dpi_awareness()  # same as the console app, so computer-use clicks line up on scaled displays
    protocol = sys.stdout
    output_lock = threading.Lock()

    def emit(message):
        with output_lock:
            protocol.write(json.dumps(message, ensure_ascii=False) + "\n")
            protocol.flush()

    # Library status messages must not corrupt the JSON protocol on stdout.
    sys.stdout = sys.stderr
    commands = queue.Queue(maxsize=20)
    stopped = threading.Event()
    approvals = Approvals(emit)

    def read_input():
        try:
            for line in sys.stdin:
                try:
                    message = json.loads(line)
                    if not isinstance(message, dict):
                        continue
                    if message.get("type") == "approval":
                        resolved = approvals.resolve(message.get("token"), message.get("approved"))
                        emit({"type": "notice", "text": "Approval received." if resolved
                              else "That approval expired or is no longer pending."})
                    elif message.get("type") == "command" and isinstance(message.get("text"), str):
                        try:
                            commands.put_nowait(message)
                        except queue.Full:
                            emit({"type": "notice", "text": "Too many queued commands. Try again shortly."})
                except (ValueError, TypeError):
                    emit({"type": "notice", "text": "Invalid bridge message."})
        finally:
            stopped.set()
            approvals.cancel_all()

    def reply(text):
        emit({"type": "reply", "text": str(text)})

    with contextlib.redirect_stdout(sys.stderr):
        jarvis = Jarvis(config.load(), on_reply=reply, on_confirm=approvals.request)
        threading.Thread(target=read_input, daemon=True).start()
        emit({"type": "ready"})
        while not stopped.is_set():
            try:
                message = commands.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                jarvis.handle(message["text"])
            except Exception as exc:
                # Full local trace; only a concise error goes to Telegram.
                import traceback

                traceback.print_exc()
                reply(f"Command failed ({type(exc).__name__}). Check the PC's Jarvis log.")
            finally:
                emit({"type": "done", "id": message.get("id")})


if __name__ == "__main__":
    main()
