"""Real-Windows smoke test (run in CI on windows-latest): python tests/smoke_windows.py"""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.tools.wininput import enable_dpi_awareness  # noqa: E402

enable_dpi_awareness()  # as jarvis/__main__.py does, before any screen code

from jarvis import tools  # noqa: E402
from jarvis.tools import apps, computer, uia  # noqa: E402


def check(name, args):
    ok, out = tools.run(name, args)
    print(f"{name}: ok={ok} -> {str(out)[:200]!r}")
    assert ok, out
    return out


def cu(name, args):
    ok, out = computer.run(name, args, confirm=lambda reason: False)
    print(f"computer.{name}: ok={ok} -> {out if isinstance(out, str) else type(out).__name__}")
    assert ok, out
    return out


print(f"{len(apps.start_apps())} Start menu apps found")
proc = subprocess.Popen(["notepad.exe"])
try:
    for _ in range(40):
        if any("notepad" in w["process"].lower() for w in apps.windows()):
            break
        time.sleep(0.25)
    check("list_windows", {})
    check("ui_list_controls", {"window": "notepad"})
    check("ui_set_text", {"window": "notepad", "text": "Hello from Jarvis {1}"})
    text = check("ui_read_text", {"window": "notepad"})
    assert "Hello from Jarvis {1}" in text, text
    check("clipboard_set", {"text": "jarvis-clip"})
    assert check("clipboard_get", {}) == "jarvis-clip"
    check("system_status", {})
    check("screenshot", {})

    # Computer use: click Notepad's text area by screen position, type, read it back through UI Automation.
    check("focus_window", {"window": "notepad"})
    _, root = uia._window_control("notepad")
    rect = uia._find(root, want_text=True).BoundingRectangle
    geo = computer._computer.geometry()
    print(f"screen {geo.width}x{geo.height} -> screenshots {geo.shot_size}")
    shot = cu("screenshot", {})
    assert shot.png_b64
    x, y = geo.to_shot((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2)
    cu("left_click", {"coordinate": [x, y]})
    cu("key", {"text": "ctrl+a"})
    cu("type", {"text": "Typed by computer use: cafe {ok}"})
    text = check("ui_read_text", {"window": "notepad"})
    assert "Typed by computer use: cafe {ok}" in text, text
    assert "x=" in cu("cursor_position", {})
    cu("zoom", {"region": [x - 100, y - 50, x + 100, y + 50]})
finally:
    subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)
print("SMOKE TEST PASSED")
