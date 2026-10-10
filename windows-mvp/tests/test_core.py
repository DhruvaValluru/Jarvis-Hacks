"""Platform-independent tests: python -m pytest tests (or python tests/test_core.py)."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jarvis import router, tools  # noqa: E402
from jarvis.tools import registry  # noqa: E402

# Multi-PC tests live in test_remote.py; importing them here runs them in test.bat (the shared spec).
from test_remote import *  # noqa: E402,F401,F403


def test_router():
    cases = {
        "Hey Jarvis, open Spotify": ("open_app", {"name": "spotify"}),
        "jarvis open youtube.com": ("open_url", {"url": "youtube.com"}),
        "set volume to 30%": ("set_volume", {"percent": 30}),
        "next song": ("media_control", {"action": "next"}),
        "pause the music": ("media_control", {"action": "play_pause"}),
        "search for weather in Raleigh": ("web_search", {"query": "weather in raleigh"}),
        "what time is it": ("system_status", {}),
        "close notepad": ("close_window", {"window": "notepad"}),
        "timer for 5 minutes": ("set_timer", {"seconds": 300, "message": "Your timer is done."}),
    }
    for text, expected in cases.items():
        assert router.route(text) == expected, text
    # Compound or open-ended requests go to Claude.
    assert router.route("open spotify and play my focus playlist") is None
    assert router.route("put the excel numbers into my report") is None


def test_registry_schemas():
    names = {s["name"] for s in tools.schemas()}
    assert {"open_app", "ui_click", "ui_set_text", "excel_read", "run_powershell"} <= names
    for s in tools.schemas():
        assert s["input_schema"]["type"] == "object"
        assert set(s["input_schema"]["required"]) <= set(s["input_schema"]["properties"])


def test_confirmation_gates():
    calls = []
    registry.tool("_probe", "test", {"x": {"type": "string"}}, risk="confirm")(lambda x: calls.append(x) or "ran")
    ok, out = tools.run("_probe", {"x": "1"}, confirm=lambda reason: False)
    assert not ok and not calls
    ok, out = tools.run("_probe", {"x": "2"}, confirm=lambda reason: True)
    assert ok and out == "ran" and calls == ["2"]
    assert registry.TOOLS["ui_click"].confirmation_reason({"window": "Slack", "name": "Send"})
    assert not registry.TOOLS["ui_click"].confirmation_reason({"window": "Spotify", "name": "Play"})
    del registry.TOOLS["_probe"]


def test_brain_loop():
    from jarvis import brain as brain_mod
    from jarvis.config import Config

    brain_mod.active_window = lambda: None
    registry.tool("_echo", "test", {"msg": {"type": "string"}}, risk="read")(lambda msg: f"echo {msg}")
    replies = [
        SimpleNamespace(stop_reason="tool_use", content=[
            SimpleNamespace(type="text", text="On it."),
            SimpleNamespace(type="tool_use", id="t1", name="_echo", input={"msg": "hi"}),
        ]),
        SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="All done.")]),
    ]
    sent = []

    class FakeMessages:
        def create(self, **kw):
            sent.append(kw)
            return replies[len(sent) - 1]

    cfg = Config("key", "claude-opus-5-5", "low", "base.en", False, False, 5)
    b = brain_mod.Brain(cfg, confirm=lambda r: True, status=lambda s: None)
    b.client = SimpleNamespace(messages=FakeMessages())
    assert b.ask("say hi") == "All done."
    tool_result = b.messages[2]["content"][0]  # user, assistant(tool_use), user(results), assistant
    assert tool_result == {"type": "tool_result", "tool_use_id": "t1", "content": "echo hi"}
    assert sent[0]["output_config"] == {"effort": "low"}
    del registry.TOOLS["_echo"]


def test_spec_lock_is_exact():
    from jarvis import doctor

    pins = doctor.lock_pins()
    assert {"anthropic", "uiautomation", "faster-whisper", "pywin32"} <= set(pins)
    assert all(v and v[0].isdigit() for v in pins.values())


class FakeScreen:
    """Stands in for the Windows backend: records input instead of sending it."""

    def __init__(self, size=(1920, 1080), element="", password=False, desktop="Default"):
        self.w, self.h = size
        self.element, self.password, self.desktop = element, password, desktop
        self.pos = (500, 500)
        self.events = []

    def size(self):
        return self.w, self.h

    def grab(self):
        from PIL import Image as PILImage

        return PILImage.new("RGB", (self.w, self.h), "white")

    def input_desktop(self):
        return self.desktop

    def cursor(self):
        return self.pos

    def move(self, x, y):
        self.pos = (x, y)
        self.events.append(("move", x, y))

    def button(self, button, down):
        self.events.append(("button", button, down))

    def wheel(self, notches, horizontal=False):
        self.events.append(("wheel", notches, horizontal))

    def key(self, vk, down):
        self.events.append(("key", vk, down))

    def vk_for_char(self, char):
        return None

    def type_text(self, text):
        self.events.append(("type", text))

    def element_at(self, x, y):
        return self.element

    def focused_is_password(self):
        return self.password


def test_computer_geometry_and_keys():
    from jarvis.tools.computer import Geometry, parse_combo

    g = Geometry(1920, 1080)
    assert g.shot_size == (1280, 720)
    assert g.to_screen(640, 360) == (960, 540)
    assert g.to_screen(5000, -10) == (1919, 0)  # clamped to the screen
    assert g.to_shot(960, 540) == (640, 360)
    assert Geometry(1024, 768).shot_size == (1024, 768)  # small screens aren't upscaled
    assert Geometry(3840, 2160).to_screen(1280 // 2, 720 // 2) == (1920, 1080)
    assert parse_combo("ctrl+s") == [0x11, ord("S")]
    assert parse_combo("Return") == [0x0D]
    assert parse_combo("alt+Tab") == [0x12, 0x09]
    assert parse_combo("super") == [0x5B]
    assert parse_combo("F5") == [0x74]
    assert parse_combo("ctrl+shift+Page_Down") == [0x11, 0x10, 0x22]
    try:
        parse_combo("hyper+q")
        raise AssertionError("unknown key should fail")
    except registry.ToolError:
        pass


def test_computer_actions():
    from jarvis.tools import computer

    fake = FakeScreen()
    c = computer.Computer(fake)
    ok, img = c.run("screenshot", {})
    assert ok and isinstance(img, registry.Image)
    import base64
    import io

    from PIL import Image as PILImage

    assert PILImage.open(io.BytesIO(base64.b64decode(img.png_b64))).size == (1280, 720)

    fake.events.clear()
    ok, out = c.run("left_click", {"coordinate": [640, 360], "text": "shift"})
    assert ok and out == "OK", out
    assert fake.events == [
        ("move", 960, 540), ("key", 0x10, True), ("button", "left", True), ("button", "left", False),
        ("key", 0x10, False),
    ]
    fake.events.clear()
    assert c.run("double_click", {"coordinate": [10, 10]})[0]
    assert [e for e in fake.events if e[0] == "button"].count(("button", "left", True)) == 2
    fake.events.clear()
    assert c.run("scroll", {"scroll_direction": "down", "scroll_amount": 3, "coordinate": [0, 0]})[0]
    assert fake.events[-1] == ("wheel", -3, False)
    fake.events.clear()
    assert c.run("key", {"text": "ctrl+s"})[0]
    assert fake.events == [("key", 0x11, True), ("key", 0x53, True), ("key", 0x53, False), ("key", 0x11, False)]
    assert c.run("type", {"text": "hello"})[0] and fake.events[-1] == ("type", "hello")
    ok, out = c.run("cursor_position", {})
    assert ok and out == "x=0, y=0", out  # the last move went to screen (0, 0)
    ok, zoomed = c.run("zoom", {"region": [0, 0, 320, 180]})
    assert ok and PILImage.open(io.BytesIO(base64.b64decode(zoomed.png_b64))).size == (481, 271)
    ok, out = c.run("left_click_drag", {"coordinate": [10, 10]})
    assert not ok and "start_coordinate" in out
    ok, out = c.run("teleport", {})
    assert not ok and "Unknown" in out


def test_computer_safety():
    from jarvis.tools import computer

    # Risky targets ask first, like ui_click.
    fake = FakeScreen(element='Button "Send"')
    c = computer.Computer(fake)
    asked = []
    ok, out = c.run("left_click", {"coordinate": [100, 100]}, confirm=lambda r: asked.append(r) or False)
    assert not ok and "declined" in out and asked and not any(e[0] == "button" for e in fake.events)
    ok, _ = c.run("left_click", {"coordinate": [100, 100]}, confirm=lambda r: True)
    assert ok and ("button", "left", True) in fake.events

    # Never types into password fields.
    fake = FakeScreen(password=True)
    ok, out = computer.Computer(fake).run("type", {"text": "hunter2"})
    assert not ok and "password" in out and not fake.events

    # Locked screen / UAC prompt: refuse instead of acting blind.
    ok, out = computer.Computer(FakeScreen(desktop="Winlogon")).run("screenshot", {})
    assert not ok and "locked" in out

    # Moving the mouse into the top-left corner takes control back.
    fake = FakeScreen()
    c = computer.Computer(fake)
    assert c.run("mouse_move", {"coordinate": [100, 100]})[0]
    fake.pos = (0, 0)  # the user slams the mouse into the corner
    try:
        c.run("left_click", {"coordinate": [200, 200]})
        raise AssertionError("should abort")
    except computer.Aborted:
        pass
    c.new_session()  # a new request starts fresh, even if the mouse is still in the corner
    assert c.run("screenshot", {})[0]


def test_brain_computer_use():
    from jarvis import brain as brain_mod
    from jarvis.config import Config
    from jarvis.tools import computer

    brain_mod.active_window = lambda: None
    fake = FakeScreen()
    computer.set_backend(fake)

    def block(id_, name, **inp):
        return SimpleNamespace(type="tool_use", id=id_, name=name, toolset_name="computer", input=inp)

    replies = [
        SimpleNamespace(stop_reason="tool_use", content=[
            block("t1", "screenshot"),
            block("t2", "left_click", coordinate=[640, 360]),
            block("t3", "type", text="hi"),
        ]),
        SimpleNamespace(stop_reason="tool_use", content=[
            block("t4", "key", text="hyper+q"),  # fails: unknown key
            block("t5", "type", text="never typed"),
        ]),
        SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="Done.")]),
    ]
    sent = []

    class FakeMessages:
        def create(self, **kw):
            sent.append(kw)
            return replies[len(sent) - 1]

    cfg = Config("key", "claude-opus-5-5", "low", "base.en", False, False, 5, computer_use=True)
    b = brain_mod.Brain(cfg, confirm=lambda r: True, status=lambda s: None)
    b.client = SimpleNamespace(messages=FakeMessages())
    assert b.ask("draw something") == "Done."

    tools_sent = sent[0]["tools"]
    assert {"type": "computer_toolset_20260801"} in tools_sent
    assert "screenshot" not in {t.get("name") for t in tools_sent}  # custom tool removed: it would clash

    first, second = b.messages[2]["content"], b.messages[4]["content"]
    assert [r["tool_use_id"] for r in first] == ["t1", "t2", "t3"]
    assert all(r["toolset_name"] == "computer" for r in first + second)
    assert first[0]["content"][0]["type"] == "image" and first[1]["content"] == "OK"
    assert second[0]["is_error"] and second[1] == computer.halted_result("t5")
    assert ("type", "hi") in fake.events and ("type", "never typed") not in fake.events
    computer.set_backend(None)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
