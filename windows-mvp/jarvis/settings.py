"""Alfred's saved settings, kept outside the install folder.

Plain settings go in %LOCALAPPDATA%\\Jarvis\\settings.env as KEY=VALUE lines (the names in
.env.example). Secrets (API keys, bot tokens) go in Windows Credential Manager as "Alfred/<KEY>",
protected by the user's Windows sign-in, and never into a file or a log. config.load() reads
both; its docstring has the order.
"""

import importlib
import os
import re
import sys
import threading
from pathlib import Path

from . import config

SECRET_KEYS = frozenset({
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "TELEGRAM_BOT_TOKEN",
    "SPECTRUM_PROJECT_SECRET", "JARVIS_TOKEN", "VOICE_SERVICE_TOKEN",
})
CRED_PREFIX = "Alfred/"
CRED_MAX_BYTES = 5 * 512  # CRED_MAX_CREDENTIAL_BLOB_SIZE
_KEY = re.compile(r"[A-Z][A-Z0-9_]*")
_NOT_FOUND = 1168  # ERROR_NOT_FOUND


def is_secret(key: str) -> bool:
    return key in SECRET_KEYS


def hint(secret: str) -> str:
    """'sk-ant-api03-...9f2c' -> 'sk-ant-...9f2c': enough to recognise a key, useless to anyone else."""
    if not secret:
        return ""
    if len(secret) < 12:
        return "(set)"
    prefix = re.match(r"[A-Za-z]{2,4}-(?:[A-Za-z]{2,5}-)?", secret)
    return f"{prefix.group(0) if prefix else ''}...{secret[-4:]}"


class MemoryBackend:
    """Secrets kept in memory only: for tests, and PCs without Credential Manager."""

    persistent = False

    def __init__(self, values: dict | None = None):
        self._values = dict(values or {})
        self._lock = threading.Lock()

    def get(self, key: str) -> str | None:
        with self._lock:
            return self._values.get(key)

    def set(self, key: str, value: str) -> None:
        with self._lock:
            self._values[key] = value

    def delete(self, key: str) -> None:
        with self._lock:
            self._values.pop(key, None)


class WinCredBackend:
    """Windows Credential Manager: generic credentials "Alfred/<KEY>" for this user on this PC."""

    persistent = True

    def get(self, key: str) -> str | None:
        import win32cred

        try:
            cred = win32cred.CredRead(CRED_PREFIX + key, win32cred.CRED_TYPE_GENERIC, 0)
        except Exception as e:  # pywintypes.error; not imported by name, which a frozen app may not resolve
            if _winerror(e) == _NOT_FOUND:
                return None
            raise
        blob = cred.get("CredentialBlob") or b""
        if isinstance(blob, str):
            return blob
        try:
            return blob.decode("utf-16-le")
        except UnicodeDecodeError:
            return blob.decode("utf-8", "replace")

    def set(self, key: str, value: str) -> None:
        import win32cred

        if len(value.encode("utf-16-le")) > CRED_MAX_BYTES:
            raise ValueError(f"{key} is too long to store.")
        win32cred.CredWrite({
            "Type": win32cred.CRED_TYPE_GENERIC,
            "TargetName": CRED_PREFIX + key,
            "UserName": key,
            # pywin32 takes the blob as text and stores it as UTF-16, which get() decodes.
            "CredentialBlob": value,
            "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
            "Comment": "Alfred setting",
        }, 0)

    def delete(self, key: str) -> None:
        import win32cred

        try:
            win32cred.CredDelete(CRED_PREFIX + key, win32cred.CRED_TYPE_GENERIC, 0)
        except Exception as e:  # pywintypes.error (see get)
            if _winerror(e) != _NOT_FOUND:
                raise


def _winerror(error: Exception) -> int | None:
    """The Windows error code of a pywin32 error (pywintypes.error), None for anything else."""
    code = getattr(error, "winerror", None)
    return code if isinstance(code, int) else None


def default_backend():
    """Credential Manager on Windows; anywhere else secrets only last as long as this process."""
    if sys.platform == "win32":
        try:
            importlib.import_module("win32cred")
            # CredRead turns a saved key's LastWritten time into a datetime with this, imported from C code.
            # Without it every saved key fails to read, so keep keys in memory rather than break.
            importlib.import_module("win32timezone")
            return WinCredBackend()
        except ImportError:
            pass
    return MemoryBackend()


class Store:
    """Read and change Alfred's settings. set()/get() send secret keys to the secret store."""

    def __init__(self, path=None, secrets=None):
        path = path or os.environ.get("JARVIS_SETTINGS_FILE")  # set by AlfredService for its engine
        self.path = Path(path) if path else config.data_dir() / "settings.env"
        self.secrets = secrets if secrets is not None else default_backend()
        self._lock = threading.RLock()

    def values(self) -> dict[str, str]:
        """Every plain setting in settings.env. Secret keys there are ignored, never used."""
        return {k: v for k, v in config.read_env_file(self.path).items() if not is_secret(k)}

    def get(self, key: str, default=None):
        _check(key)
        if is_secret(key):
            return self.get_secret(key) or default
        return self.values().get(key, default)

    def set(self, key: str, value) -> None:
        """Save a setting. An empty value removes it."""
        _check(key)
        value = "" if value is None else str(value).strip()
        if "\n" in value or "\r" in value:
            raise ValueError(f"{key} must be one line.")
        if is_secret(key):
            self.set_secret(key, value)
        elif value:
            self._write(key, value)
        else:
            self._write(key, None)

    def unset(self, key: str) -> None:
        _check(key)
        if is_secret(key):
            self.delete_secret(key)
        else:
            self._write(key, None)

    def has_secret(self, key: str) -> bool:
        return bool(self.get_secret(key))

    def get_secret(self, key: str) -> str | None:
        _check_secret(key)
        return self.secrets.get(key) or None

    def set_secret(self, key: str, value) -> None:
        _check_secret(key)
        value = "" if value is None else str(value).strip()
        if "\n" in value or "\r" in value:
            raise ValueError(f"{key} must be one line.")
        if value:
            self.secrets.set(key, value)
        else:
            self.secrets.delete(key)

    def delete_secret(self, key: str) -> None:
        _check_secret(key)
        self.secrets.delete(key)

    def secret_hint(self, key: str) -> str:
        """A safe way to show which key is saved, e.g. "sk-ant-...9f2c"; "" when none is."""
        return hint(self.get_secret(key) or "")

    def as_child_env(self, keys) -> dict[str, str]:
        """Saved values for these keys, for a child process's environment. Unset keys are left out."""
        saved = self.values()
        out = {}
        for key in keys:
            _check(key)
            value = self.get_secret(key) if is_secret(key) else saved.get(key)
            if value:
                out[key] = value
        return out

    def import_legacy_env(self, path) -> list[str]:
        """Copy an old windows-mvp\\.env into these settings (secrets to the secret store).

        The file itself is not changed. Returns the names of the keys imported.
        """
        imported = []
        for key, value in config.read_env_file(Path(path)).items():
            if _KEY.fullmatch(key) and "\n" not in value:
                self.set(key, value)
                imported.append(key)
        return imported

    def _write(self, key: str, value: str | None) -> None:
        """Replace, add (value) or remove (None) one line, keeping the rest of the file as it is."""
        with self._lock:
            try:
                lines = self.path.read_text(encoding="utf-8-sig").splitlines()
            except FileNotFoundError:
                lines = []
            out, written = [], False
            for line in lines:
                name = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
                if name == key:
                    if value is not None and not written:
                        out.append(f"{key}={value}")
                        written = True
                    continue
                out.append(line)
            if value is not None and not written:
                out.append(f"{key}={value}")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            tmp.write_text("".join(line + "\n" for line in out), encoding="utf-8")
            os.replace(tmp, self.path)


def _check(key: str) -> None:
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise ValueError(f"Not a setting name: {key!r}")


def _check_secret(key: str) -> None:
    _check(key)
    if not is_secret(key):
        raise ValueError(f"{key} is not a secret setting.")
