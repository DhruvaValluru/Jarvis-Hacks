"""Multi-PC tests on one machine: a server on localhost plays the "other PC".

python -m pytest tests (or python tests/test_remote.py)
"""

import socket
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import config, remote, tools  # noqa: E402
from jarvis.tools import registry  # noqa: E402

TOKEN = "test-token-1234567890"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _cfg(**kw) -> config.Config:
    base = dict(api_key="", model="m", effort="low", whisper_model="base.en", speak=False, fallbacks=False,
                max_steps=5, token=TOKEN, serve_host="127.0.0.1")
    return config.Config(**{**base, **kw})


def _setup():
    """Serve this process's tools as 'laptop', then connect to it as 'desktop'."""
    remote.DEVICES.clear()
    for name in [n for n, t in registry.TOOLS.items() if t.remote]:
        del registry.TOOLS[name]
    ran = []
    registry.tool("_r_echo", "echo", {"msg": {"type": "string"}}, ["msg"], risk="read")(
        lambda msg: ran.append(msg) or f"echo {msg}")
    registry.tool("_r_danger", "needs confirm", {"x": {"type": "string"}}, risk="confirm")(
        lambda x="": ran.append("danger") or "boom")
    registry.tool("_r_maybe", "confirm if x=send", {"x": {"type": "string"}},
                  confirm_if=lambda a: "sending" if a.get("x") == "send" else None)(
        lambda x="": ran.append(f"maybe {x}") or "ok")
    registry.tool("_r_img", "image", risk="read")(lambda: registry.Image("aGk=", "a picture"))
    port = _free_port()
    server = remote.serve(_cfg(name="laptop", port=port), log=lambda s: None, background=True)
    remote.connect(_cfg(name="desktop", devices={"laptop": f"127.0.0.1:{port}"}), log=lambda s: None)
    return server, ran


def _teardown(server):
    server.shutdown()
    for name in ["_r_echo", "_r_danger", "_r_maybe", "_r_img", "copy_file"]:
        registry.TOOLS.pop(name, None)
    for name in [n for n, t in registry.TOOLS.items() if t.remote]:
        del registry.TOOLS[name]
    remote.DEVICES.clear()


def test_parse_devices():
    assert config.parse_devices("laptop=100.64.0.2, Gaming PC=gaming:9000") == {
        "laptop": "100.64.0.2", "gaming-pc": "gaming:9000"}
    assert config.parse_devices("laptop=100.64.0.2,computer-2=10.0.0.5:8765") == {
        "laptop": "100.64.0.2", "computer-2": "10.0.0.5:8765"}
    d = remote.Device("x", "http://10.0.0.5:9000/", "t")
    assert (d.host, d.port) == ("10.0.0.5", 9000)
    d = remote.Device("x", "laptop", "t")
    assert (d.host, d.port) == ("laptop", 8765)


def test_remote_calls():
    server, ran = _setup()
    try:
        names = set(tools.TOOLS)
        assert {"laptop__open_app", "laptop___r_echo", "copy_file"} <= names
        assert "laptop___file_get" not in names  # hidden tools aren't offered
        assert "laptop__copy_file" not in names
        schema_names = {s["name"] for s in tools.schemas()}
        assert "_file_get" not in schema_names and "laptop___r_echo" in schema_names

        ok, out = tools.run("laptop___r_echo", {"msg": "hi"})
        assert ok and out == "echo hi"

        # confirm-tier: asked here; declined never reaches the other PC
        ok, out = tools.run("laptop___r_danger", {"x": "1"}, confirm=lambda r: False)
        assert not ok and "danger" not in ran
        ok, out = tools.run("laptop___r_danger", {"x": "1"}, confirm=lambda r: True)
        assert ok and out == "boom"

        # per-call rule lives on the other PC: it asks us back
        asked = []
        ok, out = tools.run("laptop___r_maybe", {"x": "send"}, confirm=lambda r: asked.append(r) or False)
        assert not ok and asked == ["on laptop: sending"] and "maybe send" not in ran
        ok, out = tools.run("laptop___r_maybe", {"x": "send"}, confirm=lambda r: True)
        assert ok and "maybe send" in ran
        ok, out = tools.run("laptop___r_maybe", {"x": "play"}, confirm=None)
        assert ok

        ok, out = tools.run("laptop___r_img", {})
        assert ok and isinstance(out, registry.Image) and out.png_b64 == "aGk=" and "laptop" in out.caption

        # the server refuses unconfirmed confirm-tier calls even from a client that skips asking
        resp = remote.DEVICES["laptop"].request("POST", "/call", {"name": "_r_danger", "args": {}})
        assert "needs_confirm" in resp

        # wrong token
        bad = remote.Device("laptop", f"127.0.0.1:{server.server_address[1]}", "nope")
        ok, out = registry.run("laptop___r_echo", {"msg": "x"})  # still fine with the right one
        assert ok
        try:
            bad.request("GET", "/tools")
            raise AssertionError("bad token accepted")
        except registry.ToolError as e:
            assert "token" in str(e)

        # keep-alive: many calls reuse one connection and stay fast
        start = time.perf_counter()
        for i in range(50):
            assert tools.run("laptop___r_echo", {"msg": str(i)})[0]
        assert (time.perf_counter() - start) / 50 < 0.02  # catches the ~40 ms Nagle delay
    finally:
        _teardown(server)


def test_copy_file(tmp_path):
    server, _ = _setup()
    try:
        src = tmp_path / "build.txt"
        src.write_bytes(b"hello \x00 world")
        dest_dir = tmp_path / "out"
        dest_dir.mkdir()
        ok, out = tools.run("copy_file", {"from_pc": "laptop", "from_path": str(src),
                                          "to_pc": "here", "to_path": str(dest_dir)}, confirm=lambda r: True)
        assert ok, out
        assert (dest_dir / "build.txt").read_bytes() == b"hello \x00 world"
        ok, out = tools.run("copy_file", {"from_pc": "desktop", "from_path": str(src),
                                          "to_pc": "Computer Two", "to_path": str(dest_dir)}, confirm=lambda r: True)
        assert not ok and "Unknown PC" in out
    finally:
        _teardown(server)


def test_split_target():
    remote.here = "desktop"
    remote.DEVICES.clear()
    remote.DEVICES["laptop"] = remote.Device("laptop", "x", "t")
    remote.DEVICES["computer-2"] = remote.Device("computer-2", "x", "t")
    try:
        assert remote.split_target("open spotify on the laptop") == ("laptop", "open spotify")
        assert remote.split_target("on laptop, open spotify") == ("laptop", "open spotify")
        assert remote.split_target("on computer two pause the music") == ("computer-2", "pause the music")
        assert remote.split_target("tell computer 2 to lock the computer") == ("computer-2", "lock the computer")
        assert remote.split_target("open notepad on desktop") == ("desktop", "open notepad")
        assert remote.split_target("turn it up on spotify") == (None, "turn it up on spotify")
        assert remote.device_of("laptop__open_app") == "laptop" and remote.device_of("open_app") == "desktop"
    finally:
        remote.DEVICES.clear()


def test_brain_runs_pcs_in_parallel():
    from jarvis import brain as brain_mod

    brain_mod.active_window = lambda: None
    remote.here = "desktop"
    remote.DEVICES.clear()
    order = []

    def slow(tag):
        def fn(args, confirm):
            time.sleep(0.3)
            order.append(tag)
            return tag
        return fn

    registry.TOOLS["laptop__slow"] = registry.Tool("laptop__slow", "s", slow("laptop"), risk="read", remote=True)
    registry.TOOLS["pc2__slow"] = registry.Tool("pc2__slow", "s", slow("pc2"), risk="read", remote=True)
    calls = [SimpleNamespace(type="tool_use", id="a", name="laptop__slow", input={}),
             SimpleNamespace(type="tool_use", id="b", name="pc2__slow", input={})]
    try:
        b = brain_mod.Brain(_cfg(api_key="k"), confirm=lambda r: True, status=lambda s: None)
        start = time.perf_counter()
        results = b._run_calls(calls)
        assert time.perf_counter() - start < 0.55  # both at once, not 0.6 s back to back
        assert [r["tool_use_id"] for r in results] == ["a", "b"]
        assert [r["content"] for r in results] == ["laptop", "pc2"]
    finally:
        del registry.TOOLS["laptop__slow"], registry.TOOLS["pc2__slow"]


if __name__ == "__main__":
    import tempfile

    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            if "tmp_path" in fn.__code__.co_varnames[: fn.__code__.co_argcount]:
                with tempfile.TemporaryDirectory() as d:
                    fn(Path(d))
            else:
                fn()
            print("ok", name)
