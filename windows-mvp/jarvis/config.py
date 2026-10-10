"""Settings, read from environment variables and an optional .env file."""

import os
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path

MVP_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Minimal .env reader: KEY=VALUE lines; real env vars win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


def data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    path = Path(base) / "Jarvis"
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


def load() -> Config:
    _load_dotenv(MVP_DIR / ".env")
    computer_use = os.environ.get("JARVIS_COMPUTER_USE", "1") != "0"
    return Config(
        api_key=os.environ.get("ANTHROPIC_API_KEY", "") or os.environ.get("ANTHROPIC_AUTH_TOKEN", ""),
        model=os.environ.get("JARVIS_MODEL", "claude-opus-5-5"),
        effort=os.environ.get("JARVIS_EFFORT", "low"),
        whisper_model=os.environ.get("JARVIS_WHISPER_MODEL", "base.en"),
        speak=os.environ.get("JARVIS_SPEAK", "1") != "0",
        fallbacks=os.environ.get("JARVIS_FALLBACKS", "1") != "0",
        # Computer use works one screenshot and click at a time, so it needs more steps.
        max_steps=int(os.environ.get("JARVIS_MAX_STEPS", "40" if computer_use else "12")),
        computer_use=computer_use,
        name=_pc_name(os.environ.get("JARVIS_NAME") or socket.gethostname()),
        devices=parse_devices(os.environ.get("JARVIS_DEVICES", "")),
        token=os.environ.get("JARVIS_TOKEN", ""),
        serve=os.environ.get("JARVIS_SERVE", "0") == "1",
        serve_host=os.environ.get("JARVIS_SERVE_HOST", "0.0.0.0"),
        port=int(os.environ.get("JARVIS_PORT", "8765")),
        discover=os.environ.get("JARVIS_DISCOVER", "1") != "0",
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
