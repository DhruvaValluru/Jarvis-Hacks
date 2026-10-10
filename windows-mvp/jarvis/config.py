"""Settings: the environment, windows-mvp\\.env (from source), and Alfred's saved settings (see settings.py)."""

import os
import re
import socket
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

MVP_DIR = Path(__file__).resolve().parent.parent


def frozen() -> bool:
    """True in the installed app (a bundled exe), False when running from source."""
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    """The installed app's folder, or windows-mvp when running from source."""
    return Path(sys.executable).resolve().parent if frozen() else MVP_DIR


def settings_location() -> str:
    """Where the user changes settings, for messages that tell them to."""
    return "Alfred settings" if frozen() else "windows-mvp\\.env"


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines. Comments, junk and empty values are skipped (an empty value means unset)."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return {}
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value:
            out[key] = value
    return out


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    path = Path(base) / "Jarvis"
    path.mkdir(parents=True, exist_ok=True)
    return path


def models_dir() -> Path:
    """Downloaded models (speech recognition) live here, outside the install folder."""
    path = data_dir() / "models"
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class Config:
    api_key: str
    model: str
    effort: str
    whisper_model: str
    speak: bool
    fallbacks: bool
    max_steps: int
    computer_use: bool = False
    # Multi-PC (see jarvis/remote.py)
    name: str = "this-pc"
    devices: dict = field(default_factory=dict)  # {"laptop": "100.64.0.2:8765"}
    token: str = ""
    serve: bool = False
    serve_host: str = "0.0.0.0"
    port: int = 8765
    discover: bool = True

    @property
    def has_llm(self) -> bool:
        return bool(self.api_key)


def lookup(store=None) -> Callable[..., str]:
    """get(key, default) that takes each key from the first place that has it:

    1. the process environment (a variable that is set but empty still wins),
    2. windows-mvp\\.env, only when running from source,
    3. %LOCALAPPDATA%\\Jarvis\\settings.env,
    4. the secret store (Windows Credential Manager),
    5. the default.
    JARVIS_IGNORE_USER_SETTINGS=1 skips 3 and 4 (tests and test.bat). Plain .env values are
    copied into os.environ as before, so other code can still read them; secrets never are.
    """
    from . import settings

    legacy = {} if frozen() else read_env_file(MVP_DIR / ".env")
    for key, value in legacy.items():
        if not settings.is_secret(key):
            os.environ.setdefault(key, value)
    if os.environ.get("JARVIS_IGNORE_USER_SETTINGS") == "1":
        store, saved = None, {}
    else:
        store = store if store is not None else settings.Store()
        saved = store.values()

    def get(key: str, default: str = "") -> str:
        if key in os.environ:
            return os.environ[key]
        if key in legacy:
            return legacy[key]
        if key in saved:
            return saved[key]
        if store is not None and settings.is_secret(key):
            return store.get_secret(key) or default
        return default

    return get


def child_env(keys, store=None) -> dict[str, str]:
    """The non-empty values of these keys (same order as load()), to pass to a child process."""
    get = lookup(store)
    return {key: value for key in keys if (value := get(key))}


def load(store=None) -> Config:
    get = lookup(store)
    computer_use = get("JARVIS_COMPUTER_USE", "1") != "0"
    return Config(
        api_key=get("ANTHROPIC_API_KEY") or get("ANTHROPIC_AUTH_TOKEN"),
        model=get("JARVIS_MODEL", "claude-opus-5-5"),
        effort=get("JARVIS_EFFORT", "low"),
        whisper_model=get("JARVIS_WHISPER_MODEL", "base.en"),
        speak=get("JARVIS_SPEAK", "1") != "0",
        fallbacks=get("JARVIS_FALLBACKS", "1") != "0",
        # Computer use works one screenshot and click at a time, so it needs more steps.
        max_steps=int(get("JARVIS_MAX_STEPS") or ("40" if computer_use else "12")),
        computer_use=computer_use,
        name=_pc_name(get("JARVIS_NAME") or socket.gethostname()),
        devices=parse_devices(get("JARVIS_DEVICES")),
        token=get("JARVIS_TOKEN"),
        serve=get("JARVIS_SERVE", "0") == "1",
        serve_host=get("JARVIS_SERVE_HOST", "0.0.0.0"),
        port=int(get("JARVIS_PORT") or "8765"),
        discover=get("JARVIS_DISCOVER", "1") != "0",
    )


def _pc_name(name: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", name.strip().lower()).strip("-")[:30] or "pc"


def parse_devices(text: str) -> dict:
    """'laptop=100.64.0.2, desktop=desktop:8765' -> {'laptop': '100.64.0.2', 'desktop': 'desktop:8765'}"""
    out = {}
    for item in re.split(r"[,;\n]+", text.strip()):
        if "=" in item:
            name, address = item.split("=", 1)
            if name.strip() and address.strip():
                out[_pc_name(name)] = address.strip()
    return out
