"""Platform-independent tests: python -m pytest tests (or python tests/test_core.py)."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import router, tools  # noqa: E402
from jarvis.tools import registry  # noqa: E402


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


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
