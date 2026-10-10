"""Local JSON-lines bridge. Only the parent Telegram process connects to it.

Commands run serially on the main Windows user-session thread. A separate
reader accepts approval replies while a command is waiting for confirmation.
"""

import contextlib
import json
import os
import queue
import secrets
import sys
import threading

from . import config, consent
from .__main__ import Jarvis
from .tools.registry import audit


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


def release_held_input() -> list[str]:
    """After /stop killed the previous worker mid-action, let go of anything it was holding."""
    if sys.platform != "win32":
        return []
    from .tools.wininput import enable_dpi_awareness, release_all

    enable_dpi_awareness()  # same as the console app, so computer-use clicks line up on scaled displays
    try:
        return release_all()
    except Exception:  # noqa: BLE001 - never block startup on this
        return []


def main():
    after_stop = os.environ.get("JARVIS_RESTART_REASON") == "stop"
    released = release_held_input()
    if after_stop:
        audit("telegram_stop", {}, True, f"hard stop from Telegram; released: {', '.join(released) or 'nothing held'}")
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

    if not consent.accepted():
        # Only the person at the PC can accept the data notice (jarvis.bat --terms).
        emit({"type": "notice", "text": consent.NOT_ACCEPTED})
        sys.exit(3)

    with contextlib.redirect_stdout(sys.stderr):
        jarvis = Jarvis(config.load(), on_reply=reply, on_confirm=approvals.request)
        threading.Thread(target=read_input, daemon=True).start()
        emit({"type": "ready", "after_stop": after_stop, "released": len(released)})
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
