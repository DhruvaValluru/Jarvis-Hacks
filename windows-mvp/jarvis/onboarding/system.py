"""This PC, for the setup: a quick check (shown as information; only an unsupported Windows blocks)
and "Start Alfred when I sign in", a value in HKCU\\...\\Run that Settings shows and can remove.
Windows modules (winreg, sounddevice) are imported only when used."""

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from .. import config

MIN_BUILD = 17763  # Windows 10 version 1809; Alfred is tested on Windows 11 and Server 2025
SERVER_YEARS = {14393: "2016", 17763: "2019", 20348: "2022", 26100: "2025"}  # Windows Server's builds
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Alfred"


def windows() -> dict:
    """{"windows", "supported", "text"} for this OS."""
    if sys.platform != "win32":
        return {"windows": False, "supported": False, "text": f"{platform.system() or 'This system'} isn't Windows."}
    try:
        build = sys.getwindowsversion().build
    except (AttributeError, OSError):
        build = 0
    name = "Windows 11" if build >= 22000 else "Windows 10" if build >= 10240 else "An older Windows"
    try:
        edition = platform.win32_edition() or ""
    except Exception:  # noqa: BLE001
        edition = ""
    if edition.startswith("Server"):  # ServerDatacenter, ServerStandard, ...: not a Windows 10 or 11
        name, edition = f"Windows Server {SERVER_YEARS.get(build, '')}".strip(), ""
    edition = {"Professional": "Pro", "Core": "Home"}.get(edition, edition)
    text = f"{name} {edition}".strip() + (f" (build {build})" if build else "")
    return {"windows": True, "supported": build >= MIN_BUILD, "text": text}


def check(*, any_os: bool = False) -> dict:
    """The welcome page's check. any_os: the stand-in engine (--fake-core), which runs anywhere."""
    os_info = windows()
    supported = os_info["supported"]
    if supported:
        os_detail = f"{os_info['text']}. Alfred works here."
    elif any_os:
        os_detail = f"{os_info['text']} The stand-in engine (--fake-core) works anyway; nothing happens on this PC."
    else:
        os_detail = f"{os_info['text']} Alfred needs Windows 10 (version 1809 or later) or Windows 11."
    checks = [
        {"name": "windows", "label": "Windows", "ok": supported, "detail": os_detail},
        _item("microphone", "Microphone", probe(has_microphone),
              "Found one. You can talk to Alfred.",
              "None found. Voice is optional: typing always works.",
              "Couldn't check: the voice extras aren't installed. Typing always works."),
        _item("office", "Microsoft Office", probe(has_office),
              "Found Word or Excel. Alfred can read and fill in your documents.",
              "Not found. Alfred's Office features stay off; everything else works.",
              "Couldn't check. Office is optional."),
        _item("tailscale", "Tailscale", probe(has_tailscale),
              "Found. Your PCs can reach each other on any network.",
              "Not found. Only needed to use your PCs across different networks; on the same Wi-Fi it isn't.",
              "Couldn't check. Tailscale is optional."),
    ]
    return {"supported": supported, "blocking": not supported and not any_os, "os": os_info["text"], "checks": checks}


def _item(name, label, ok, yes, no, unknown) -> dict:
    return {"name": name, "label": label, "ok": ok, "detail": yes if ok else unknown if ok is None else no}


def probe(fn) -> bool | None:
    """True or False, None if it couldn't be checked (a missing module, not Windows, an error)."""
    try:
        return bool(fn())
    except Exception:  # noqa: BLE001
        return None


def has_microphone() -> bool:
    import sounddevice as sd

    return any(d["max_input_channels"] > 0 for d in sd.query_devices())


def has_office() -> bool:
    import winreg

    for prog in ("Excel.Application", "Word.Application"):
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, prog))
            return True
        except OSError:
            pass
    return False


def has_tailscale() -> bool:
    if shutil.which("tailscale"):
        return True
    if sys.platform != "win32":
        return False
    folders = {os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramW6432")}
    return any(Path(f, "Tailscale", "tailscale.exe").is_file() for f in folders if f)


# --- start when the user signs in ------------------------------------------------
class RunKey:
    """HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run: per user, no admin rights, and listed
    in Task Manager's Startup apps, where it can be turned off too."""

    def get(self, name: str) -> str | None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
                value, _ = winreg.QueryValueEx(key, name)
        except OSError:
            return None
        return str(value)

    def set(self, name: str, value: str) -> None:
        import winreg

        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)

    def delete(self, name: str) -> None:
        import winreg

        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
                winreg.DeleteValue(key, name)
        except FileNotFoundError:
            pass


class PretendRunKey:
    """The stand-in engine's startup list (--fake-core): kept in memory, Windows' own list is never touched."""

    simulated = True

    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)

    def set(self, name: str, value: str) -> None:
        self.values[name] = value

    def delete(self, name: str) -> None:
        self.values.pop(name, None)


def launch_command() -> str:
    """How Windows starts Alfred at sign-in: the installed app, or this Python with the source folder."""
    if config.frozen():
        return subprocess.list2cmdline([sys.executable])
    exe = Path(sys.executable)
    windowless = exe.with_name("pythonw.exe")
    code = (f"import runpy,sys;sys.path.insert(0,{str(config.MVP_DIR)!r});"
            "runpy.run_module('jarvis.app',run_name='__main__',alter_sys=True)")
    return subprocess.list2cmdline([str(windowless if windowless.is_file() else exe), "-c", code])


class Autostart:
    """Start Alfred when the user signs in. Off unless they turn it on; Alfred opens its window as usual."""

    def __init__(self, key=None):
        self.key = key  # tests pass a fake RunKey; --fake-core a PretendRunKey

    @property
    def supported(self) -> bool:
        return self.key is not None or sys.platform == "win32"

    def _key(self):
        if self.key is None:
            self.key = RunKey()
        return self.key

    def status(self) -> dict:
        if not self.supported:
            return {"supported": False, "enabled": False, "command": ""}
        key = self._key()
        try:
            value = key.get(RUN_VALUE)
        except Exception:  # noqa: BLE001 - shown as off
            value = None
        return {"supported": True, "enabled": bool(value), "command": value or launch_command(),
                "where": f"HKCU\\{RUN_KEY}", "simulated": getattr(key, "simulated", False) is True}

    def set(self, on: bool) -> dict:
        if not self.supported:
            raise OSError("Starting with Windows only works on Windows.")
        if on:
            self._key().set(RUN_VALUE, launch_command())
        else:
            self._key().delete(RUN_VALUE)
        return self.status()
