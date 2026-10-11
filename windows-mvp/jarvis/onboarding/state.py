"""How far the first-run setup got: data_dir()/onboarding.json.

  {"version": 1, "steps": {"welcome": "done", "anthropic": "skipped", ...}, "completed_at": "2026-10-10T09:30:00"}

A step is "done" or "skipped"; a step that isn't listed hasn't been answered yet. completed_at is set
when the user presses Done on the last step; until then the app opens the setup again (resuming at
the first unanswered step). Every step can be revisited later from Settings. The stand-in engine
(--fake-core) keeps its own fake-onboarding.json, so trying the setup there never finishes the real one.
"""

import json
import os
import threading
import time
from pathlib import Path

from .. import config

VERSION = 1
DONE, SKIPPED = "done", "skipped"
STEPS = ("welcome", "consent", "import", "anthropic", "safety", "speech", "openai", "telegram", "pcs", "finish")
REQUIRED = frozenset({"welcome", "consent", "safety", "finish"})  # never skipped
TITLES = {
    "welcome": "Welcome", "consent": "Your data", "import": "Old settings", "anthropic": "Claude",
    "safety": "Safety", "speech": "Voice", "openai": "OpenAI", "telegram": "Telegram",
    "pcs": "Other PCs", "finish": "Finish",
}


class SetupState:
    """Reads and writes onboarding.json. The file is replaced atomically, so a crash never leaves half of it."""

    def __init__(self, path=None, *, name: str = "onboarding.json"):
        self._path = Path(path) if path else None
        self.name = name  # in data_dir(), when no path is given
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path or config.data_dir() / self.name

    def exists(self) -> bool:
        return self.path.is_file()

    def read(self) -> dict:
        """The saved state, cleaned: unknown steps and values are dropped."""
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        steps = data.get("steps") if isinstance(data.get("steps"), dict) else {}
        done_at = data.get("completed_at")
        return {
            "version": VERSION,
            "steps": {name: steps[name] for name in STEPS if steps.get(name) in (DONE, SKIPPED)},
            "completed_at": done_at if isinstance(done_at, str) and done_at else None,
        }

    def mark(self, step: str, status: str) -> dict:
        """Record a step as done or skipped. Finishing the last step completes the setup."""
        if step not in STEPS or status not in (DONE, SKIPPED):
            raise ValueError(f"Not a setup step and status: {step!r} {status!r}")
        with self._lock:
            data = self.read()
            data["steps"][step] = status
            if step == "finish" and status == DONE:
                data["completed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._write(data)
        return data

    def _write(self, data: dict) -> None:
        path = self.path
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)
