"""Telegram worker for jarvis-telegram.bat: the Alfred engine (engine.py) speaking the older protocol v1.

The Node bridge (telegram/index.mjs in legacy mode) starts it and talks JSON lines:
  in   command{id,text}  approval{token,approved}
  out  ready{after_stop,released}  reply{text}  notice{text}  approval{token,reason,timeout}  done{id}
The Alfred app runs the bridge in parent mode instead, on its one engine (jarvis/service/bridge.py).
"""

import sys

from .engine import EXPIRED, Approvals, Engine, release_held_input
from .engine import main as engine_main

__all__ = ["Approvals", "LegacyWorker", "main", "release_held_input"]  # Approvals: tests import it from here


class LegacyWorker(Engine):
    default_stop_source = "telegram"
    serve = False  # jarvis.bat may already serve this PC to the others, and two servers can't share the port

    def ready(self, after_stop, released):
        self.emit({"type": "ready", "after_stop": after_stop, "released": len(released)})

    def accepted(self, message):
        pass

    def rejected(self, message):
        self.notice("Too many queued commands. Try again shortly.")

    def status(self, kind, text):
        print(f"  {text}", file=sys.stderr)

    def reply(self, text):
        self.emit({"type": "reply", "text": str(text)})

    def announce(self, text, source=None):
        self.reply(text)

    def approval_context(self):
        return {}

    def approval_closed(self, token, approved, by):
        pass

    def approval_answered(self, resolved):
        self.notice("Approval received." if resolved else EXPIRED)

    def done(self, message, ok):
        self.emit({"type": "done", "id": message.get("id")})

    def failed(self, exc):
        return f"Command failed ({type(exc).__name__}). Check the PC's Jarvis log."

    def remote_log(self, text):
        if not (self.cfg.token and self.cfg.token in text):
            print(f" {text}", file=sys.stderr)


def main():
    sys.exit(engine_main(LegacyWorker))


if __name__ == "__main__":
    main()
