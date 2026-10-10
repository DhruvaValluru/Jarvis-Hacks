"""`python -m jarvis --doctor`: check this machine matches the shared test spec.

Everyone (people, agents, CI) should get the same SPEC fingerprint. If yours
differs, your results aren't comparable: re-run setup.bat.
"""

import hashlib
import importlib.metadata as md
import os
import platform
import re
import sys

from . import config

SPEC_VERSION = "2"
PYTHON = (3, 12)
# Settings every test run uses (test.bat sets these; CI too).
TEST_PROFILE = {
    "JARVIS_MODEL": "claude-opus-5-5",
    "JARVIS_EFFORT": "low",
    "JARVIS_WHISPER_MODEL": "base.en",
    "JARVIS_FALLBACKS": "1",
    "JARVIS_COMPUTER_USE": "1",
    "JARVIS_MAX_STEPS": "40",
}
NODE_MAJOR = 22  # Telegram bridge (windows-mvp/telegram, exact deps in package-lock.json)


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def lock_pins() -> dict[str, str]:
    pins = {}
    for line in (config.MVP_DIR / "requirements.lock.txt").read_text().splitlines():
        if "==" in line and not line.startswith("#"):
            name, ver = line.strip().split("==")
            pins[_norm(name)] = ver
    return pins


def check() -> tuple[bool, list[str]]:
    lines, ok = [], True
    installed = {_norm(d.metadata["Name"]): d.version for d in md.distributions()}

    py = sys.version_info[:2]
    good = py == PYTHON
    ok &= good
    lines.append(f"[{'ok' if good else 'XX'}] Python {platform.python_version()} (spec {PYTHON[0]}.{PYTHON[1]}.x)")

    win = platform.system() == "Windows"
    lines.append(f"[{'ok' if win else '--'}] OS {platform.platform()}")

    bad = []
    for name, ver in lock_pins().items():
        have = installed.get(name)
        if have != ver:
            bad.append(f"{name} {have or 'missing'} (spec {ver})")
    ok &= not bad
    lines.append(f"[{'ok' if not bad else 'XX'}] Packages match requirements.lock.txt" + ("" if not bad else ":"))
    lines += [f"       {b}" for b in bad]

    cfg = config.load()
    drift = [f"{k}={os.environ.get(k, '(default)')}" for k, v in TEST_PROFILE.items() if os.environ.get(k, v) != v]
    lines.append(f"[{'ok' if not drift else '!!'}] Test profile" + (" differs: " + ", ".join(drift) if drift else ""))
    lines.append(f"[{'ok' if cfg.has_llm else '--'}] API key {'set' if cfg.has_llm else 'not set (Claude tests skipped)'}")

    node = _node_major()
    if node is None:
        lines.append("[--] Node.js not found (only needed for the Telegram bridge)")
    else:
        good = node == NODE_MAJOR
        ok &= good
        lines.append(f"[{'ok' if good else 'XX'}] Node.js {node} (spec {NODE_MAJOR})")

    if win:
        lines.append(_optional("Office (COM)", _has_office))
        lines.append(_optional("Microphone", _has_mic))

    # Hash what is actually installed, so any drift gives a different fingerprint.
    actual = sorted((n, installed.get(n)) for n in lock_pins())
    profile = sorted((k, os.environ.get(k, v)) for k, v in TEST_PROFILE.items())
    fp = hashlib.sha256(f"{SPEC_VERSION}|{py}|{actual}|{profile}|node{node}".encode()).hexdigest()[:12]
    lines.append(f"SPEC v{SPEC_VERSION} fingerprint {fp} -> {'MATCHES SPEC' if ok else 'DOES NOT MATCH SPEC'}")
    return ok, lines


def _node_major() -> int | None:
    import subprocess

    try:
        out = subprocess.run(["node", "--version"], capture_output=True, text=True, timeout=10).stdout
        return int(out.strip().lstrip("v").split(".")[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _optional(label, probe) -> str:
    try:
        return f"[{'ok' if probe() else '--'}] {label}"
    except Exception:  # noqa: BLE001
        return f"[--] {label}"


def _has_office() -> bool:
    import winreg

    for prog in ("Excel.Application", "Word.Application"):
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog))
            return True
        except OSError:
            pass
    return False


def _has_mic() -> bool:
    import sounddevice as sd

    return any(d["max_input_channels"] > 0 for d in sd.query_devices())


def main() -> int:
    ok, lines = check()
    print("\n".join(lines))
    return 0 if ok else 1
