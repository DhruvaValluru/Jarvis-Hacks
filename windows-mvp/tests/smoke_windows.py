"""Real-Windows smoke test (run in CI on windows-latest): python tests/smoke_windows.py"""

import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import tools  # noqa: E402
from jarvis.tools import apps  # noqa: E402


def check(name, args):
    ok, out = tools.run(name, args)
    print(f"{name}: ok={ok} -> {str(out)[:200]!r}")
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
finally:
    subprocess.run(["taskkill", "/F", "/IM", "notepad.exe"], capture_output=True)
print("SMOKE TEST PASSED")
