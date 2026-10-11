"""Link the user's Telegram account to Alfred without typing an ID.

The setup shows a 6-digit code and restarts the Telegram bridge in discovery mode: with no owner it
runs nothing and only reports who messaged the bot and what they typed (telegram_user events). The
first Telegram user who sends exactly that code becomes TELEGRAM_OWNER_ID and the bridge restarts
with that owner. Everyone else is ignored. The code works for 10 minutes and once; five wrong
codes end the attempt, so nobody can guess it.

Nothing is saved until the right code arrives: discovery mode is only an override in the running app
(service.telegram_discovery), never a change to the saved owner. So when the link is cancelled,
expires or locks, and also when Alfred quits or the PC restarts while a code is on screen, the owner
from before stays, and Telegram goes back to how it was (stopped again if it wasn't running).
"""

import hmac
import os
import threading
import time

from .. import config
from .validators import LINK_CODE, TELEGRAM_ID, new_link_code

TTL = 600
MAX_WRONG = 5
NEEDS = ("TELEGRAM_BOT_TOKEN", "SPECTRUM_PROJECT_SECRET")
RUNNING = ("starting", "discovery", "running")  # Telegram states in which the bridge runs


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
        self._was_running = False  # whether the bridge ran before the first code: put back when no one links
        self._unsubscribe = None
        self._timer = None
        self._lock = threading.Lock()  # never call status() (or anything that takes it) while holding it
        self._bridge_lock = threading.Lock()  # bridge restarts one at a time, in order

    def start(self) -> dict:
        """A new code; the bridge (re)starts in discovery mode until the right person sends it."""
        saved = config.lookup(self.store)
        if not all(self.store.get(key) or saved(key) for key in NEEDS):
            raise LinkError("Save the bot token and the Photon Spectrum secret first.")
        if _set_outside("TELEGRAM_OWNER_ID"):
            raise LinkError("TELEGRAM_OWNER_ID is set in windows-mvp\\.env or the environment, which win over "
                            "Alfred's settings. Remove it there first.")
        running = self.service.status().telegram in RUNNING
        with self._lock:
            if self.state != "waiting":
                self._was_running = running
            self._close()
            self.code, self.owner, self.wrong = new_link_code(), "", 0
            self._deadline = self.clock() + TTL
            self.state = "waiting"
            self._unsubscribe = self.service.subscribe(self._on_event)
            self._timer = threading.Timer(TTL + 1, self.status)  # ends it on time even if nobody looks
            self._timer.daemon = True
            self._timer.start()
        with self._bridge_lock:
            self.service.telegram_discovery(True)
            self._restart_bridge()
        return self.status()

    def cancel(self) -> dict:
        with self._lock:
            if self.state == "waiting":
                self.state = "expired" if self.clock() > self._deadline else "cancelled"
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
            self.store.set("TELEGRAM_OWNER_ID", user)  # the first and only thing the link saves
            self.store.set("JARVIS_TELEGRAM", "1")  # linking an account means wanting Telegram on
            self._end()
        if self.on_linked is not None:
            self.on_linked(user)

    def _end(self) -> None:
        """Stop listening; Telegram settles on another thread (with the lock held)."""
        self._close()
        threading.Thread(target=self._settle, daemon=True, name="alfred-telegram-link").start()

    def _settle(self) -> None:
        """After a link ends: the bridge with its saved owner (the new one, once linked), or stopped
        again if it wasn't running before the code. A new code meanwhile has set the bridge up itself."""
        with self._bridge_lock:
            with self._lock:
                if self.state == "waiting":
                    return
                run = self.state == "linked" or self._was_running
            self.service.telegram_discovery(False)
            if run:
                self._restart_bridge()
            else:
                self._stop_bridge()

    def _close(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _restart_bridge(self) -> None:
        try:
            if self.service.status().telegram != "off":
                self.service.stop_telegram()
            self.service.start_telegram()
        except Exception:  # noqa: BLE001 - the setup shows Telegram's state; Settings can turn it on
            pass

    def _stop_bridge(self) -> None:
        try:
            self.service.stop_telegram()
        except Exception:  # noqa: BLE001
            pass


def _set_outside(key: str) -> bool:
    """Whether the environment or windows-mvp\\.env (from source) sets key; both win over Alfred's settings."""
    legacy = {} if config.frozen() else config.read_env_file(config.MVP_DIR / ".env")
    return key in os.environ or bool(legacy.get(key))
