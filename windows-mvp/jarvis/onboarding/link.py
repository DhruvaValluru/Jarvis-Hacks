"""Link the user's Telegram account to Alfred without typing an ID.

The setup shows a 6-digit code and runs the Telegram bridge in discovery mode (no owner, so it runs
nothing; it only reports who messaged the bot and what they typed, as telegram_user events). The
first Telegram user who sends exactly that code becomes TELEGRAM_OWNER_ID and the bridge restarts
with that owner. Everyone else is ignored. The code works for 10 minutes and once; five wrong
codes end the attempt, so nobody can guess it. Cancelling puts the previous owner back.
"""

import hmac
import threading
import time

from .. import config
from .validators import LINK_CODE, TELEGRAM_ID, new_link_code

TTL = 600
MAX_WRONG = 5
NEEDS = ("TELEGRAM_BOT_TOKEN", "SPECTRUM_PROJECT_SECRET")


class LinkError(RuntimeError):
    pass


class TelegramLink:
    """state: idle, waiting, linked, expired, locked (too many wrong codes) or cancelled."""

    def __init__(self, service, store, *, on_linked=None, clock=time.monotonic):
        self.service, self.store = service, store
        self.on_linked = on_linked
        self.clock = clock
        self.state = "idle"
        self.code = ""
        self.owner = ""
        self.wrong = 0
        self._deadline = 0.0
        self._previous = ""
        self._unsubscribe = None
        self._lock = threading.Lock()

    def start(self) -> dict:
        """A new code; the bridge (re)starts in discovery mode until the right person sends it."""
        saved = config.lookup(self.store)
        if not all(self.store.get(key) or saved(key) for key in NEEDS):
            raise LinkError("Save the bot token and the Photon Spectrum secret first.")
        with self._lock:
            if self.state != "waiting":
                self._previous = self.store.get("TELEGRAM_OWNER_ID") or ""
            self._close()
            self.state = "idle"
            self.code, self.owner, self.wrong = new_link_code(), "", 0
            self._deadline = self.clock() + TTL
            self.store.unset("TELEGRAM_OWNER_ID")
            self.store.set("JARVIS_TELEGRAM", "1")
            if config.lookup(self.store)("TELEGRAM_OWNER_ID"):
                self._restore()
                raise LinkError("TELEGRAM_OWNER_ID is set in windows-mvp\\.env or the environment, which win over "
                                "Alfred's settings. Remove it there first.")
            self.state = "waiting"
            self._unsubscribe = self.service.subscribe(self._on_event)
        self._restart_bridge()
        return self.status()

    def cancel(self) -> dict:
        with self._lock:
            if self.state != "waiting":
                return self.status()
            self.state = "cancelled"
            self._end()
        return self.status()

    def status(self) -> dict:
        with self._lock:
            if self.state == "waiting" and self.clock() > self._deadline:
                self.state = "expired"
                self._end()
            waiting = self.state == "waiting"
            return {
                "state": self.state,
                "code": self.code if waiting else "",
                "expires_in": max(0, int(self._deadline - self.clock())) if waiting else 0,
                "wrong": self.wrong,
                "owner": self.owner,
            }

    def _on_event(self, event: dict) -> None:
        """On the bridge's reader thread: decide quickly, restart the bridge on another thread."""
        if event.get("type") != "telegram_user":
            return
        user = str(event.get("id") or "")
        text = "".join(str(event.get("text") or "").split())  # the setup shows "123 456"
        with self._lock:
            if self.state != "waiting":
                return
            if self.clock() > self._deadline:
                self.state = "expired"
                self._end()
                return
            if not TELEGRAM_ID.fullmatch(user):
                return
            if not hmac.compare_digest(text.encode("utf-8"), self.code.encode("utf-8")):
                if LINK_CODE.fullmatch(text):  # a wrong guess; anything else (/start, hello) doesn't count
                    self.wrong += 1
                    if self.wrong >= MAX_WRONG:
                        self.state = "locked"
                        self._end()
                return
            self.state, self.owner = "linked", user
            self.store.set("TELEGRAM_OWNER_ID", user)
            self._close()
        if self.on_linked is not None:
            self.on_linked(user)
        threading.Thread(target=self._restart_bridge, daemon=True, name="alfred-telegram-link").start()

    def _end(self) -> None:
        """Stop listening and put back the owner from before (with the lock held)."""
        self._close()
        if self._restore():
            threading.Thread(target=self._restart_bridge, daemon=True, name="alfred-telegram-link").start()

    def _restore(self) -> bool:
        if not self._previous:
            return False
        self.store.set("TELEGRAM_OWNER_ID", self._previous)
        return True

    def _close(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def _restart_bridge(self) -> None:
        try:
            if self.service.status().telegram != "off":
                self.service.stop_telegram()
            self.service.start_telegram()
        except Exception:  # noqa: BLE001 - the setup shows Telegram's state; Settings can turn it on
            pass
