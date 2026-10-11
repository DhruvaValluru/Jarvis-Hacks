"""Alfred's self-test: is this copy of Alfred complete, and does its engine run?

  alfred-cli.exe --selftest     the installed app (CI runs it on the build and on the installed copy)
  python -m jarvis.selftest     from source (the last step of test.bat)

It imports every heavy module the app needs, checks that the data files Alfred ships with are there,
then starts the engine through AlfredService with a throwaway data folder: no saved settings or keys,
no Claude, nothing shared with your other PCs. It runs "reset" and "what time is it" (answered on the
PC; it only reads the clock), presses Stop, waits for the engine to come back, and shuts it down.
Nothing else on the PC is touched (see NEEDS_WEBVIEW2 for the one module that would otherwise change a
setting). Exit code 0 when every check passed, 1 otherwise.
"""

import argparse
import contextlib
import importlib
import os
import platform
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from . import __version__, config, consent

FAST_COMMAND = "what time is it"  # router fast path -> system_status: reads the clock, no Claude
SOURCE = "selftest"  # how the commands and the Stop show up in the engine's events and audit log

# Alfred's own modules: they must import everywhere.
MODULES = (
    "jarvis.engine", "jarvis.tools", "jarvis.service", "jarvis.app.server", "jarvis.app.main",
    "jarvis.brain", "jarvis.voice", "jarvis.remote", "jarvis.telegram_worker", "jarvis.uninstall",
)
# What the app, the engine and the tools import, often only when first used (so a module missing from
# the bundle would otherwise only show up when someone needs it). Checked on Windows only.
WINDOWS_MODULES = (
    # Claude
    "anthropic", "truststore",
    # Windows APIs, COM, UI Automation, volume
    "pythoncom", "pywintypes", "win32api", "win32con", "win32gui", "win32process", "win32job", "win32cred",
    "win32clipboard", "win32com.client", "comtypes.client", "comtypes.stream", "pycaw.pycaw", "uiautomation",
    "jarvis.tools.wininput", "jarvis.tools.computer",
    # Speech recognition (local Whisper) and the microphone
    "numpy", "sounddevice", "faster_whisper", "ctranslate2", "onnxruntime", "av", "tokenizers", "huggingface_hub",
    # The window (pywebview on .NET WinForms + Edge WebView2) and the tray icon
    "PIL.Image", "PIL.ImageDraw", "psutil", "clr", "webview", "webview.platforms.winforms",
    "webview.platforms.edgechromium", "pystray", "pystray._win32",
)
# pywebview's WinForms backend picks its web engine when it's imported. Without the WebView2 Runtime it picks
# Internet Explorer and writes IE settings for this program into the registry (HKCU\...\FeatureControl), so the
# self-test only imports it when the runtime is there. edgechromium only loads the WebView2 interop DLLs.
NEEDS_WEBVIEW2 = ("webview.platforms.winforms",)
# (package, file inside it): data files and DLLs that are loaded by path, not imported.
FILES = (
    ("jarvis.app", "ui/index.html"), ("jarvis.app", "ui/app.js"), ("jarvis.app", "ui/app.css"),
    ("jarvis.app", "ui/icon.svg"),
)
WINDOWS_FILES = (
    ("faster_whisper", "assets/*.onnx"),  # the voice activity model
    ("uiautomation", "bin/UIAutomationClient_VC140_X64.dll"),
    ("webview", "js/api.js"),
    ("webview", "lib/Microsoft.Web.WebView2.Core.dll"),
    ("webview", "lib/Microsoft.Web.WebView2.WinForms.dll"),
    # pywebview's Edge backend wants all three on import, whatever this PC is.
    ("webview", "lib/runtimes/win-x64/native/WebView2Loader.dll"),
    ("webview", "lib/runtimes/win-x86/native/WebView2Loader.dll"),
    ("webview", "lib/runtimes/win-arm64/native/WebView2Loader.dll"),
    ("_sounddevice_data", "portaudio-binaries/libportaudio64bit.dll"),
    ("pythonnet", "runtime/Python.Runtime.dll"),
    ("clr_loader", "ffi/dlls/amd64/ClrLoader.dll"),
)
WEBVIEW2_CLIENT = r"Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"


@dataclass
class Check:
    name: str
    ok: bool | None  # None: not checked here (not needed on this PC or in this mode)
    detail: str = ""

    def line(self) -> str:
        mark = "--" if self.ok is None else ("ok" if self.ok else "XX")
        return f"[{mark}] {self.name}" + (f": {self.detail}" if self.detail else "")


# --- modules and files -------------------------------------------------------
def import_checks(modules: Iterable[str], importer: Callable = importlib.import_module) -> Iterator[Check]:
    for name in modules:
        started = time.monotonic()
        try:
            importer(name)
        except BaseException as e:  # noqa: BLE001 - SystemExit or a .NET error must not end the self-test
            if isinstance(e, KeyboardInterrupt):
                raise
            yield Check(f"import {name}", False, f"{type(e).__name__}: {e}"[:300])
        else:
            yield Check(f"import {name}", True, _seconds(started))


def package_dir(name: str) -> Path:
    """The folder a package's files are in (inside _internal in the installed app)."""
    module = importlib.import_module(name)
    paths = list(getattr(module, "__path__", None) or [])
    return Path(paths[0]) if paths else Path(module.__file__).parent


def file_checks(files: Iterable[tuple[str, str]], locate: Callable[[str], Path] = package_dir) -> Iterator[Check]:
    for package, relative in files:
        name = f"file {package}/{relative}"
        try:
            folder = locate(package)
        except Exception as e:  # noqa: BLE001
            yield Check(name, False, f"can't find {package} ({type(e).__name__}: {e})"[:300])
            continue
        found = sorted(folder.glob(relative)) if "*" in relative else [folder / relative]
        found = [path for path in found if path.is_file()]
        yield Check(name, bool(found), str(found[0]) if found else f"missing in {folder}")


def path_check(name: str, path: Path, *, folder: bool = False) -> Check:
    ok = path.is_dir() if folder else path.is_file()
    return Check(name, ok, str(path) if ok else f"missing: {path}")


def bundle_checks() -> Iterator[Check]:
    """What the app needs besides modules: the UI, the data notice, the spec's package list, the exes."""
    yield from file_checks(FILES)
    yield path_check("PRIVACY.md (the data notice)", config.MVP_DIR / "PRIVACY.md")
    yield path_check("requirements.lock.txt (what --doctor checks)", config.MVP_DIR / "requirements.lock.txt")
    if config.frozen():
        for exe in ("Alfred.exe", "alfred-cli.exe"):
            yield path_check(exe, config.app_dir() / exe)


def windows_checks(sys_platform: str = sys.platform, importer: Callable = importlib.import_module,
                   locate: Callable[[str], Path] = package_dir) -> Iterator[Check]:
    if sys_platform != "win32":
        yield Check("Windows modules and files", None, "not checked: this isn't Windows")
        return
    webview2 = _webview2_runtime()  # first: it decides whether NEEDS_WEBVIEW2 may be imported
    if config.frozen():
        # Generated from Windows' UI Automation type library at build time (packaging/build.ps1). Checked
        # first: importing comtypes.client adds a cache folder in %TEMP% to where it could also be found.
        yield from import_checks(["comtypes.gen.UIAutomationClient"], importer)
    for name in WINDOWS_MODULES:
        if name in NEEDS_WEBVIEW2 and not webview2.ok:
            yield Check(f"import {name}", None, "not checked without the WebView2 Runtime (importing it would change "
                                                "Internet Explorer settings in the registry)")
        else:
            yield from import_checks([name], importer)
    yield from file_checks(WINDOWS_FILES, locate)
    if not config.frozen():
        yield _uia_type_library()
    yield webview2


def _uia_type_library() -> Check:
    try:
        import comtypes.client

        module = comtypes.client.GetModule("UIAutomationCore.dll")
        return Check("UI Automation type library (comtypes)", True, module.__name__)
    except Exception as e:  # noqa: BLE001
        return Check("UI Automation type library (comtypes)", False, f"{type(e).__name__}: {e}"[:300])


def webview2_version() -> str:
    """The Edge WebView2 Runtime's version, or "" when it isn't installed (for the machine or this user).

    Where Microsoft documents it for 64-bit Windows, which is also where pywebview looks: whenever this finds
    it, pywebview uses Edge and not Internet Explorer (NEEDS_WEBVIEW2)."""
    import winreg

    places = ((winreg.HKEY_LOCAL_MACHINE, "SOFTWARE\\WOW6432Node\\" + WEBVIEW2_CLIENT),
              (winreg.HKEY_CURRENT_USER, "Software\\" + WEBVIEW2_CLIENT))
    for root, key in places:
        try:
            with winreg.OpenKey(root, key) as handle:
                version = str(winreg.QueryValueEx(handle, "pv")[0])
        except OSError:
            continue
        if version and version != "0.0.0.0":
            return version
    return ""


def _webview2_runtime() -> Check:
    try:
        version = webview2_version()
    except Exception as e:  # noqa: BLE001
        return Check("Edge WebView2 Runtime", None, f"couldn't tell ({type(e).__name__})")
    if version:
        return Check("Edge WebView2 Runtime", True, version)
    return Check("Edge WebView2 Runtime", None, "not installed: Alfred opens in the web browser instead of its window")


# --- Node.js and the Telegram bridge -----------------------------------------
def node_checks(run: Callable = subprocess.run) -> Iterator[Check]:
    """The bridge runs on the bundled node.exe in the installed app, on Node from PATH from source."""
    from .doctor import NODE_MAJOR
    from .service.bridge import bridge_command

    node, script = bridge_command()
    bundled = config.frozen()
    folder = Path(script).parent
    yield path_check("Telegram bridge (telegram/index.mjs)", Path(script))
    try:
        out = run([node, "--version"], capture_output=True, text=True, timeout=30, **_no_window())
        version = (out.stdout or "").strip()
        if out.returncode != 0 or not version.startswith("v"):
            raise OSError(f"exit code {out.returncode}: {(out.stderr or version).strip()[:200]}")
    except (OSError, subprocess.SubprocessError) as e:
        if bundled:
            yield Check("Node.js (node\\node.exe)", False, f"{node}: {e}")
        else:
            yield Check("Node.js", None, "not found on PATH: only needed for Telegram (setup-telegram.bat)")
        return
    on_spec = version.lstrip("v").split(".")[0] == str(NODE_MAJOR)
    yield Check("Node.js", on_spec, f"{version} at {node}" + ("" if on_spec else f" (spec: Node {NODE_MAJOR})"))
    modules = folder / "node_modules"
    if not modules.is_dir():
        if bundled:
            yield Check("Telegram bridge packages", False, f"missing: {modules}")
        else:
            yield Check("Telegram bridge packages", None, "telegram\\node_modules missing: run setup-telegram.bat")
        return
    # Loading spectrum.mjs loads the bridge's npm packages; it has no side effects of its own.
    target = (folder / "spectrum.mjs").as_uri()
    try:
        out = run([node, "--input-type=module", "-e", "await import(process.argv[1])", target], cwd=str(folder),
                  capture_output=True, text=True, timeout=60, **_no_window())
        ok, detail = out.returncode == 0, (out.stderr or "").strip()[-300:]
    except (OSError, subprocess.SubprocessError) as e:
        ok, detail = False, str(e)
    yield Check("Telegram bridge packages load", ok, "spectrum-ts" if ok else detail)


def _no_window() -> dict:
    return {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # CREATE_NO_WINDOW


# --- the engine ----------------------------------------------------------------
@contextlib.contextmanager
def isolated_env(folder: Path):
    """Run the engine as if on a fresh PC: its own data folder, none of this PC's saved settings or keys
    (Credential Manager), no Claude, no speech, nothing shared with other PCs, no Telegram."""
    values = {
        "LOCALAPPDATA": str(folder),  # config.data_dir(): settings, consent and the audit log go here
        "JARVIS_IGNORE_USER_SETTINGS": "1",
        "JARVIS_ACCEPT_TERMS": consent.TERMS_VERSION,  # an automated test: nothing leaves the PC
        "ANTHROPIC_API_KEY": "", "ANTHROPIC_AUTH_TOKEN": "",  # set but empty wins over saved keys
        "JARVIS_SPEAK": "0", "JARVIS_SERVE": "0", "JARVIS_TOKEN": "", "JARVIS_DEVICES": "", "JARVIS_DISCOVER": "0",
        "JARVIS_TEST_TOOLS": "", "TELEGRAM_BOT_TOKEN": "", "SPECTRUM_PROJECT_SECRET": "",
    }
    saved = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield folder
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def engine_checks(timeout: float | None = None, engine_cmd=None) -> Iterator[Check]:
    from .service import AlfredService
    from .settings import MemoryBackend, Store

    timeout = timeout or (120.0 if config.frozen() else 60.0)  # first start of an install: virus scans
    with tempfile.TemporaryDirectory(prefix="alfred-selftest-", ignore_cleanup_errors=True) as tmp:
        with isolated_env(Path(tmp)) as folder:
            service = AlfredService(Store(folder / "settings.env", MemoryBackend()), engine_cmd=engine_cmd)
            try:
                yield from _drive(service, timeout)
            finally:
                service.shutdown(10)
            state = service.status().engine
            yield Check("Engine shuts down", state == "off", f"state {state}")


def _drive(service, timeout: float) -> Iterator[Check]:
    started = time.monotonic()
    service.start()
    ready = service.wait_ready(timeout)
    yield Check("Engine starts", ready, _seconds(started) if ready else _engine_trouble(service, timeout))
    if not ready:
        return
    yield _command(service, "reset", lambda replies: replies == ["Okay, fresh start."])
    yield _command(service, FAST_COMMAND, lambda replies: bool(replies) and replies[0].startswith("It's "))

    after = service.bus.last_seq
    started = time.monotonic()
    pressed = service.stop(SOURCE)
    back = pressed and _wait_event(service, after, lambda e: e["type"] == "ready" and e.get("after_stop"), timeout)
    back = back and service.wait_ready(timeout)
    yield Check("Stop, then a fresh engine", bool(back),
                _seconds(started) if back else _engine_trouble(service, timeout))
    if back:
        yield _command(service, "reset", lambda replies: replies == ["Okay, fresh start."], " after Stop")


def _command(service, text: str, expect: Callable[[list], bool], label: str = "") -> Check:
    name = f'Command "{text}"{label}'
    started = time.monotonic()
    try:
        result = service.run_and_wait(text, SOURCE, timeout=30)
    except (RuntimeError, ValueError) as e:  # NotReady, QueueFull: the engine went away
        return Check(name, False, f"{type(e).__name__}: {e}")
    ok = result.ok and expect(result.replies)
    said = " / ".join(result.replies)[:200]
    if result.timed_out:
        said = "no answer within 30 s"
    return Check(name, ok, f"{said} ({_seconds(started)})" if ok else said or "no reply")


def _wait_event(service, after: int, match: Callable[[dict], bool], timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for event in service.events(after, timeout=max(0.0, deadline - time.monotonic())):
            after = event["seq"]
            if match(event):
                return True
    return False


def _engine_trouble(service, timeout: float) -> str:
    status = service.status()
    parts = [f"state {status.engine} (waited up to {timeout:.0f} s)"]
    if status.last_error:
        parts.append(status.last_error)
    engine = getattr(service, "_engine", None)  # what the engine wrote to its log (stderr), if it's still there
    log = [line for line in getattr(engine, "log", []) if line.strip()][-5:]
    if log:
        parts.append("engine log: " + " | ".join(log))
    return "; ".join(parts)[:1500]


def _seconds(started: float) -> str:
    return f"{time.monotonic() - started:.1f} s"


# --- running it ----------------------------------------------------------------
SECTIONS = (
    ("Alfred's modules", lambda: import_checks(MODULES)),
    ("Bundled files", bundle_checks),
    ("Windows", windows_checks),
    ("Node.js and Telegram", node_checks),
    ("Engine", engine_checks),
)


def run(sections=None, out: Callable[[str], None] = print) -> list[Check]:
    """Run every section, printing each result as it comes (a crash mid-way still leaves a report)."""
    checks = []
    for title, section in SECTIONS if sections is None else sections:
        out(f"--- {title}")
        try:
            for check in section():
                checks.append(check)
                out(check.line())
        except Exception as e:  # noqa: BLE001 - a bug in one section must not hide the others
            check = Check(f"{title} (self-test error)", False, f"{type(e).__name__}: {e}"[:300])
            checks.append(check)
            out(check.line())
    return checks


def summary(checks: list[Check]) -> tuple[bool, str]:
    failed = [check for check in checks if check.ok is False]
    skipped = sum(check.ok is None for check in checks)
    passed = len(checks) - len(failed) - skipped
    if failed:
        names = ", ".join(check.name for check in failed)[:500]
        return False, f"RESULT: FAILED: {len(failed)} of {len(checks)} checks ({names})"
    return True, f"RESULT: PASSED ({passed} checks, {skipped} not needed here)"


def describe() -> str:
    where = f"installed app in {config.app_dir()}" if config.frozen() else f"from source in {config.MVP_DIR}"
    return (f"Alfred {__version__} self-test: {where}\n"
            f"Python {platform.python_version()} on {platform.platform()}")


def main(argv=None) -> int:
    prog = "alfred-cli.exe --selftest" if config.frozen() else "python -m jarvis.selftest"
    parser = argparse.ArgumentParser(
        prog=prog, description="Check that this copy of Alfred is complete and its engine runs.")
    parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(AttributeError, ValueError, OSError):
            stream.reconfigure(errors="replace", line_buffering=True)
    print(describe())
    checks = run()
    ok, text = summary(checks)
    print(text)
    return 0 if ok else 1


def exit_now(code: int) -> None:
    """End the process with `code`, skipping Python's exit handlers. The self-test loads .NET (pythonnet)
    and COM only to see that they load; their shutdown hooks must not hang or crash it after the report."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.flush()
    os._exit(code)


if __name__ == "__main__":
    exit_now(main())
