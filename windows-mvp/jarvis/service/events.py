"""The event feed between AlfredService and the UI: numbered events in a ring buffer."""

import contextlib
import itertools
import threading
import time
from collections import deque
from collections.abc import Callable


class EventBus:
    """Thread-safe. Every event gets the next seq number; the last `size` events are kept, so a UI
    that reconnects asks for events(after=<last seq it saw>) and catches up."""

    def __init__(self, size: int = 1000):
        self._events: deque[dict] = deque(maxlen=size)
        self._seq = 0
        self._cond = threading.Condition()
        self._subscribers: list[Callable[[dict], None]] = []

    @property
    def last_seq(self) -> int:
        return self._seq

    def publish(self, type: str, **fields) -> dict:
        with self._cond:
            self._seq += 1
            event = {**fields, "seq": self._seq, "ts": time.time(), "type": type}
            self._events.append(event)
            self._cond.notify_all()
            subscribers = list(self._subscribers)
        for fn in subscribers:
            with contextlib.suppress(Exception):  # a broken subscriber must not stop the feed
                fn(event)
        return event

    def subscribe(self, fn: Callable[[dict], None]) -> Callable[[], None]:
        """Call fn(event) for every new event, on the thread that publishes it (keep fn quick).
        Returns a function that unsubscribes."""
        with self._cond:
            self._subscribers.append(fn)
        return lambda: self.unsubscribe(fn)

    def unsubscribe(self, fn: Callable[[dict], None]) -> None:
        with self._cond:
            if fn in self._subscribers:
                self._subscribers.remove(fn)

    def events(self, after: int | None = None, timeout: float = 25) -> list[dict]:
        """Events with seq > after (after=None: all that are kept). If there are none yet, waits up
        to `timeout` seconds for one (a long poll) and returns [] if nothing happened."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while True:
                found = self._since(after)
                if found:
                    return found
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._cond.wait(remaining)

    def _since(self, after: int | None) -> list[dict]:
        if not self._events:
            return []
        first = self._events[0]["seq"]
        if after is None or after > self._seq:  # None, or a cursor from before a restart: replay everything
            return list(self._events)
        return list(itertools.islice(self._events, max(0, after - first + 1), None))
