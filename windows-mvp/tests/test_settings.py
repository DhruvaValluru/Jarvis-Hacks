"""Saved settings and where each setting comes from (jarvis/settings.py, config.load). No Windows needed."""

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import config, consent, settings  # noqa: E402
from jarvis.settings import MemoryBackend, Store  # noqa: E402

TOUCHED = [
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_OWNER_ID",
    "JARVIS_TOKEN", "JARVIS_MODEL", "JARVIS_EFFORT", "JARVIS_WHISPER_MODEL", "JARVIS_SPEAK", "JARVIS_FALLBACKS",
    "JARVIS_COMPUTER_USE", "JARVIS_MAX_STEPS", "JARVIS_NAME", "JARVIS_IGNORE_USER_SETTINGS", "JARVIS_SETTINGS_FILE",
]
KEY = "sk-ant-api03-abcdefghijklmnop-9f2c"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A clean environment, LOCALAPPDATA and windows-mvp folder (with its own .env) under tmp_path."""
    saved = dict(os.environ)
    for key in TOUCHED:
        os.environ.pop(key, None)
    os.environ["LOCALAPPDATA"] = str(tmp_path / "appdata")
    mvp = tmp_path / "mvp"
    mvp.mkdir()
    monkeypatch.setattr(config, "MVP_DIR", mvp)
    yield tmp_path
    os.environ.clear()
    os.environ.update(saved)


def _store(home) -> Store:
    return Store(home / "settings.env", MemoryBackend())


def test_plain_settings_round_trip_and_keep_the_rest_of_the_file(home):
    store = _store(home)
    store.path.write_text("# my notes\nJARVIS_MODEL=old\nJARVIS_EFFORT=low\n", encoding="utf-8")
    store.set("JARVIS_MODEL", "claude-opus-5-5")
    store.set("JARVIS_SPEAK", 0)
    assert store.get("JARVIS_MODEL") == "claude-opus-5-5"
    assert store.get("JARVIS_SPEAK") == "0"
    assert store.get("JARVIS_NAME", "default") == "default"
    store.unset("JARVIS_EFFORT")
    store.set("JARVIS_SPEAK", "")  # empty means unset
    assert store.path.read_text(encoding="utf-8") == "# my notes\nJARVIS_MODEL=claude-opus-5-5\n"
    with pytest.raises(ValueError):
        store.set("JARVIS_MODEL", "two\nlines")
    with pytest.raises(ValueError):
        store.set("lower_case", "x")


def test_secrets_go_to_the_secret_store_never_to_the_file(home):
    store = _store(home)
    store.set("ANTHROPIC_API_KEY", KEY)  # set() routes secrets too
    store.set_secret("TELEGRAM_BOT_TOKEN", "123456:telegram-token-value")
    store.set("JARVIS_MODEL", "m")
    text = store.path.read_text(encoding="utf-8")
    assert "sk-ant" not in text and "telegram-token" not in text and "ANTHROPIC" not in text
    assert store.has_secret("ANTHROPIC_API_KEY") and store.get_secret("ANTHROPIC_API_KEY") == KEY
    assert store.get("ANTHROPIC_API_KEY") == KEY
    assert store.secret_hint("ANTHROPIC_API_KEY") == "sk-ant-...9f2c"
    assert store.secret_hint("TELEGRAM_BOT_TOKEN") == "...alue"
    assert store.secret_hint("OPENAI_API_KEY") == ""
    assert settings.hint("short") == "(set)"
    store.delete_secret("ANTHROPIC_API_KEY")
    assert not store.has_secret("ANTHROPIC_API_KEY")
    with pytest.raises(ValueError):
        store.set_secret("JARVIS_MODEL", "not a secret")
    # A secret pasted into settings.env by hand is ignored, never used.
    store.path.write_text(f"ANTHROPIC_API_KEY={KEY}\n", encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in store.values()


def test_memory_backend_and_automatic_choice():
    backend = MemoryBackend({"JARVIS_TOKEN": "t"})
    assert backend.get("JARVIS_TOKEN") == "t" and not backend.persistent
    backend.set("JARVIS_TOKEN", "u")
    backend.delete("JARVIS_TOKEN")
    backend.delete("JARVIS_TOKEN")  # deleting twice is fine
    assert backend.get("JARVIS_TOKEN") is None
    chosen = settings.default_backend()
    if sys.platform == "win32":
        assert isinstance(chosen, settings.WinCredBackend)
    else:
        assert isinstance(chosen, MemoryBackend)


def test_precedence_env_then_legacy_env_then_settings_then_secrets_then_defaults(home):
    (config.MVP_DIR / ".env").write_text(
        "JARVIS_MODEL=from-legacy\nJARVIS_EFFORT=from-legacy\nANTHROPIC_API_KEY=\n", encoding="utf-8")
    store = _store(home)
    store.path.write_text(
        "JARVIS_MODEL=from-settings\nJARVIS_EFFORT=from-settings\nJARVIS_WHISPER_MODEL=from-settings\n",
        encoding="utf-8")
    store.set_secret("ANTHROPIC_API_KEY", KEY)
    os.environ["JARVIS_MODEL"] = "from-env"
    cfg = config.load(store)
    assert cfg.model == "from-env"
    assert cfg.effort == "from-legacy"
    assert cfg.whisper_model == "from-settings"
    assert cfg.api_key == KEY and cfg.has_llm  # the empty line in .env doesn't hide the saved key
    assert cfg.max_steps == 40 and cfg.computer_use  # defaults unchanged (doctor.TEST_PROFILE)


def test_an_empty_environment_variable_still_wins(home):
    store = _store(home)
    store.set("JARVIS_NAME", "saved-name")
    store.set_secret("ANTHROPIC_API_KEY", KEY)
    os.environ["ANTHROPIC_API_KEY"] = ""
    os.environ["ANTHROPIC_AUTH_TOKEN"] = ""
    os.environ["JARVIS_EFFORT"] = ""
    cfg = config.load(store)
    assert cfg.api_key == "" and not cfg.has_llm
    assert cfg.effort == ""
    assert cfg.name == "saved-name"


def test_secrets_are_never_copied_into_the_environment(home):
    (config.MVP_DIR / ".env").write_text(f"ANTHROPIC_API_KEY={KEY}\nJARVIS_TOKEN=legacy-token-123456\n"
                                         "JARVIS_EFFORT=medium\n", encoding="utf-8")
    store = _store(home)
    store.set_secret("OPENAI_API_KEY", "sk-proj-openai-secret-value")
    cfg = config.load(store)
    assert cfg.api_key == KEY and cfg.token == "legacy-token-123456"
    assert "ANTHROPIC_API_KEY" not in os.environ and "JARVIS_TOKEN" not in os.environ
    assert "OPENAI_API_KEY" not in os.environ
    assert os.environ["JARVIS_EFFORT"] == "medium"  # plain .env values stay reachable as before
    assert config.child_env(["OPENAI_API_KEY", "JARVIS_EFFORT", "TELEGRAM_OWNER_ID"], store) == {
        "OPENAI_API_KEY": "sk-proj-openai-secret-value", "JARVIS_EFFORT": "medium"}
    assert store.as_child_env(["OPENAI_API_KEY", "JARVIS_EFFORT"]) == {"OPENAI_API_KEY": "sk-proj-openai-secret-value"}


def test_ignore_flag_skips_saved_settings_and_secrets(home):
    store = _store(home)
    store.set("JARVIS_MODEL", "saved")
    store.set_secret("ANTHROPIC_API_KEY", KEY)
    os.environ["JARVIS_IGNORE_USER_SETTINGS"] = "1"
    cfg = config.load(store)
    assert cfg.model == "claude-opus-5-5" and not cfg.has_llm
    del os.environ["JARVIS_IGNORE_USER_SETTINGS"]
    cfg = config.load(store)
    assert cfg.model == "saved" and cfg.has_llm


def test_the_installed_app_ignores_windows_mvp_env(home, monkeypatch):
    (config.MVP_DIR / ".env").write_text("JARVIS_MODEL=from-legacy\n", encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert config.load(_store(home)).model == "claude-opus-5-5"
    assert config.settings_location() == "Alfred settings"
    monkeypatch.setattr(sys, "frozen", False)
    assert config.settings_location() == "windows-mvp\\.env"


def test_import_legacy_env_sorts_secrets_and_leaves_the_file_alone(home):
    legacy = home / "old.env"
    legacy.write_text(
        "# Copy to .env\n"
        f"ANTHROPIC_API_KEY={KEY}\n"
        "JARVIS_MODEL=claude-opus-5-5\n"
        "OPENAI_API_KEY=\n"
        "TELEGRAM_OWNER_ID=12345\n"
        'TELEGRAM_BOT_TOKEN="123456:abcdefghijkl"\n'
        "# JARVIS_SPEAK=0\n", encoding="utf-8")
    before = legacy.read_bytes()
    store = _store(home)
    imported = store.import_legacy_env(legacy)
    assert imported == ["ANTHROPIC_API_KEY", "JARVIS_MODEL", "TELEGRAM_OWNER_ID", "TELEGRAM_BOT_TOKEN"]
    assert legacy.read_bytes() == before
    assert store.get_secret("ANTHROPIC_API_KEY") == KEY
    assert store.get_secret("TELEGRAM_BOT_TOKEN") == "123456:abcdefghijkl"
    assert store.values() == {"JARVIS_MODEL": "claude-opus-5-5", "TELEGRAM_OWNER_ID": "12345"}
    assert "sk-ant" not in store.path.read_text(encoding="utf-8")


def test_default_paths_live_under_localappdata(home):
    assert Store(secrets=MemoryBackend()).path == home / "appdata" / "Jarvis" / "settings.env"
    assert config.models_dir() == home / "appdata" / "Jarvis" / "models" and config.models_dir().is_dir()


def test_consent_records_where_it_was_accepted_and_shows_the_full_notice(home, monkeypatch):
    real = Path(__file__).resolve().parent.parent
    monkeypatch.setattr(config, "MVP_DIR", real)
    os.environ.pop("JARVIS_ACCEPT_TERMS", None)
    assert not consent.accepted()
    consent.record(source="app")
    data = json.loads((home / "appdata" / "Jarvis" / "consent.json").read_text(encoding="utf-8"))
    assert data["version"] == consent.TERMS_VERSION and data["source"] == "app"
    assert consent.accepted()
    assert consent.notice_markdown() == (real / "PRIVACY.md").read_text(encoding="utf-8")


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Credential Manager")
def test_windows_credential_manager_round_trip():
    import pywintypes

    backend, key = settings.WinCredBackend(), f"ALFRED_TEST_{os.getpid()}"
    try:
        backend.set(key, "sk-test-\u00e9-value")
    except pywintypes.error as e:
        if e.winerror == 1312:  # ERROR_NO_SUCH_LOGON_SESSION: no user profile in this session
            pytest.skip("Credential Manager isn't available in this session")
        raise
    try:
        assert backend.get(key) == "sk-test-\u00e9-value"
        backend.set(key, "second")
        assert backend.get(key) == "second"
    finally:
        backend.delete(key)
    assert backend.get(key) is None
    backend.delete(key)  # already gone: no error
