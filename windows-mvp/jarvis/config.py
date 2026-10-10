"""Settings, read from environment variables and an optional .env file."""

import os
from dataclasses import dataclass
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
    )
