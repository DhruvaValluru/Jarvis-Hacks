"""Launch, list, focus and close apps and windows."""

import difflib
import json
import os
import re
import subprocess
import time
from pathlib import Path

from .registry import ToolError, tool

# Names people say -> something Windows can start directly.
ALIASES = {
    "notepad": "notepad.exe",
    "calculator": "calc.exe",
    "calc": "calc.exe",
    "paint": "mspaint.exe",
    "settings": "ms-settings:",
    "windows settings": "ms-settings:",
    "file explorer": "explorer.exe",
    "explorer": "explorer.exe",
    "files": "explorer.exe",
    "task manager": "taskmgr.exe",
    "command prompt": "cmd.exe",
    "terminal": "wt.exe",
    "powershell": "powershell.exe",
    "control panel": "control.exe",
    "snipping tool": "ms-screenclip:",
    "store": "ms-windows-store:",
    "microsoft store": "ms-windows-store:",
    "mail": "outlookmail:",
    "calendar": "outlookcal:",
    "camera": "microsoft.windows.camera:",
    "clock": "ms-clock:",
    "spotify": "spotify:",
}

_start_apps_cache: list[dict] | None = None


def _norm(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"^(the|my)\s+", "", name)
    name = re.sub(r"\s+(app|application|program)$", "", name)
    return re.sub(r"\s+", " ", name)


def _powershell(command: str, timeout: int = 30) -> str:
    out = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return out.stdout


def _start_menu_shortcuts() -> list[dict]:
    apps = []
    roots = [
        Path(os.environ.get("ProgramData", r"C:\ProgramData")) / r"Microsoft\Windows\Start Menu\Programs",
        Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs",
    ]
    for root in roots:
        if root.is_dir():
            for p in root.rglob("*"):
                if p.suffix.lower() in (".lnk", ".url"):
                    apps.append({"Name": p.stem, "path": str(p)})
    return apps


def start_apps(refresh: bool = False) -> list[dict]:
    """Everything in the Start menu: [{'Name': ..., 'AppID': ...} or {'Name': ..., 'path': ...}]."""
    global _start_apps_cache
    if _start_apps_cache is not None and not refresh:
        return _start_apps_cache
    apps: list[dict] = []
    try:
        raw = _powershell("Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress")
        data = json.loads(raw) if raw.strip() else []
        apps = data if isinstance(data, list) else [data]
    except Exception:  # noqa: BLE001 - fall back to scanning shortcuts
        apps = []
    if not apps:
        apps = _start_menu_shortcuts()
    _start_apps_cache = [a for a in apps if a.get("Name")]
    return _start_apps_cache


def best_match(query: str, names: list[str], cutoff: float = 0.6) -> str | None:
    q = _norm(query)
    lowered = {n.lower(): n for n in names}
    if q in lowered:
        return lowered[q]
    starts = [n for n in names if n.lower().startswith(q)]
    if starts:
        return min(starts, key=len)
    words = [n for n in names if re.search(rf"\b{re.escape(q)}\b", n.lower())]
    if words:
        return min(words, key=len)
    close = difflib.get_close_matches(q, list(lowered), n=1, cutoff=cutoff)
    return lowered[close[0]] if close else None


CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def start_detached(target: str) -> bool:
    """In the Alfred app, the engine runs in a Job Object that Stop kills (jarvis/service/process.py).
    Open what the user asked for through the Windows shell outside that job, so stopping or closing
    Alfred never closes their apps. Returns False when not in the app (open it the usual way)."""
    if os.environ.get("JARVIS_ENGINE_JOB") != "1":
        return False
    try:
        subprocess.Popen(["explorer.exe", target], creationflags=CREATE_BREAKAWAY_FROM_JOB)
        return True
    except OSError:
        return False


def _launch(target: str) -> None:
    if start_detached(target):
        return
    if target.startswith("shell:AppsFolder"):
        subprocess.Popen(["explorer.exe", target])
    else:
        os.startfile(target)  # noqa: S606 - starting the user's own apps is the point


@tool(
    "open_app",
    "Open (launch) an application by name, e.g. 'Spotify', 'Word', 'Chrome', 'Notepad', 'Settings'. "
    "Searches the Start menu, so any installed app works.",
    {"name": {"type": "string", "description": "App name as the user said it"}},
    required=["name"],
)
def open_app(name: str) -> str:
    q = _norm(name)
    apps = start_apps()
    match = best_match(q, [a["Name"] for a in apps])
    if match:
        app = next(a for a in apps if a["Name"] == match)
        target = f"shell:AppsFolder\\{app['AppID']}" if app.get("AppID") else app["path"]
        _launch(target)
        return f"Opened {match}."
    if q in ALIASES:
        _launch(ALIASES[q])
        return f"Opened {name}."
    raise ToolError(f"Couldn't find an app called '{name}' in the Start menu.")


def windows() -> list[dict]:
    """Visible top-level windows with a title."""
    import psutil
    import win32gui
    import win32process

    fg = win32gui.GetForegroundWindow()
    found = []

    def visit(hwnd, _):
        if not win32gui.IsWindowVisible(hwnd):
            return True
        title = win32gui.GetWindowText(hwnd)
        if not title or title in ("Program Manager", "Windows Input Experience"):
            return True
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            proc = psutil.Process(pid).name()
        except Exception:  # noqa: BLE001
            proc = "?"
        found.append({"hwnd": hwnd, "title": title, "process": proc, "pid": pid, "active": hwnd == fg})
        return True

    win32gui.EnumWindows(visit, None)
    return found


def find_window(query: str) -> dict:
    """Find one window by title or process name (fuzzy)."""
    wins = windows()
    if not wins:
        raise ToolError("No windows are open.")
    q = _norm(query)
    for w in wins:  # exact-ish first: process name without .exe, or title contains the query
        if w["process"].lower().removesuffix(".exe") == q:
            return w
    for w in wins:
        if q in w["title"].lower():
            return w
    labels = {f'{w["title"]} {w["process"].lower().removesuffix(".exe")}': w for w in wins}
    match = best_match(q, list(labels), cutoff=0.4)
    if match:
        return labels[match]
    raise ToolError(f"No open window matches '{query}'. Open windows: " + "; ".join(w["title"] for w in wins[:15]))


@tool("list_windows", "List the open windows (title, app, which one is active).", risk="read")
def list_windows() -> str:
    lines = [
        f'{"* " if w["active"] else "  "}{w["title"]}  [{w["process"]}]' for w in windows()
    ]
    return "Open windows (* = active):\n" + "\n".join(lines)


def active_window() -> dict | None:
    """The foreground window, without enumerating every window (cheap enough to poll)."""
    try:
        import psutil
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        title = win32gui.GetWindowText(hwnd) if hwnd else ""
        if not title:
            return None
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        try:
            proc = psutil.Process(pid).name()
        except Exception:  # noqa: BLE001
            proc = "?"
        return {"hwnd": hwnd, "title": title, "process": proc, "pid": pid, "active": True}
    except Exception:  # noqa: BLE001
        return None


def idle_seconds() -> float | None:
    """Seconds since the last keyboard or mouse input on this PC (None if unknown)."""
    try:
        import ctypes

        class LASTINPUTINFO(ctypes.Structure):
            _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

        info = LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(info)
        if not ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000
    except Exception:  # noqa: BLE001 - not Windows
        return None


def bring_to_front(hwnd: int) -> None:
    import ctypes

    import win32con
    import win32gui

    if win32gui.IsIconic(hwnd):
        win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
    try:
        win32gui.SetForegroundWindow(hwnd)
    except Exception:  # noqa: BLE001 - Windows can refuse focus changes; this usually works instead
        ctypes.windll.user32.SwitchToThisWindow(hwnd, True)
    time.sleep(0.2)


@tool(
    "focus_window",
    "Bring a window to the front, by part of its title or its app name (e.g. 'Word', 'Spotify', 'Budget.xlsx').",
    {"window": {"type": "string"}},
    required=["window"],
)
def focus_window(window: str) -> str:
    w = find_window(window)
    bring_to_front(w["hwnd"])
    return f'Switched to "{w["title"]}".'


@tool(
    "close_window",
    "Close a window politely (like clicking X). The app may still ask to save changes.",
    {"window": {"type": "string", "description": "Part of the title or the app name"}},
    required=["window"],
)
def close_window(window: str) -> str:
    import win32con
    import win32gui

    w = find_window(window)
    win32gui.PostMessage(w["hwnd"], win32con.WM_CLOSE, 0, 0)
    return f'Asked "{w["title"]}" to close.'
