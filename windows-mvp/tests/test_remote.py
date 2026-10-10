"""Multi-PC tests on one machine: a server on localhost plays the "other PC".

python -m pytest tests (or python tests/test_remote.py)
"""

import os
import socket
import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis import config, discovery, remote, router, tools  # noqa: E402
from jarvis.tools import registry  # noqa: E402

TOKEN = "test-token-1234567890"
TEST_TOOLS = ["_r_echo", "_r_danger", "_r_maybe", "_r_img", "_r_new"]


def _free_port(kind=socket.SOCK_STREAM) -> int:
    with socket.socket(socket.AF_INET, kind) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _cfg(**kw) -> config.Config:
    base = dict(api_key="", model="m", effort="low", whisper_model="base.en", speak=False, fallbacks=False,
                max_steps=5, token=TOKEN, serve_host="127.0.0.1", discover=False)
    return config.Config(**{**base, **kw})


def _wait(cond, timeout=3.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


def _setup(status=None):
    """Serve this process's tools as 'laptop', then connect to it as 'desktop'."""
    _clear()
    ran = []
    registry.tool("_r_echo", "echo", {"msg": {"type": "string"}}, ["msg"], risk="read")(
        lambda msg: ran.append(msg) or f"echo {msg}")
    registry.tool("_r_danger", "needs confirm", {"x": {"type": "string"}}, risk="confirm")(
        lambda x="": ran.append("danger") or "boom")
    registry.tool("_r_maybe", "confirm if x=send", {"x": {"type": "string"}},
                  confirm_if=lambda a: "sending" if a.get("x") == "send" else None)(
        lambda x="": ran.append(f"maybe {x}") or "ok")
    registry.tool("_r_img", "image", risk="read")(lambda: registry.Image("aGk=", "a picture"))
    remote.local_status = status or (lambda: {"window": "", "in_use": False})
    port = _free_port()
    server = remote.serve(_cfg(name="laptop", port=port), log_fn=lambda s: None, background=True, status_every=0.05)
    remote.connect(_cfg(name="desktop", devices={"laptop": f"127.0.0.1:{port}"}), log_fn=lambda s: None)
    assert remote.DEVICES["laptop"].online
    return server, ran


def _clear():
    for dev in remote.DEVICES.values():
        dev.stop()
    remote.DEVICES.clear()
    remote._hub_tools_done = False
    for name in [n for n, t in list(registry.TOOLS.items()) if t.remote or n in TEST_TOOLS]:
        del registry.TOOLS[name]
    for name in ("copy_file", "send_clipboard"):
        registry.TOOLS.pop(name, None)


def _teardown(server):
    server.shutdown()
    server.server_close()
    _clear()


def test_parse_devices():
    assert config.parse_devices("laptop=100.64.0.2, Gaming PC=gaming:9000") == {
        "laptop": "100.64.0.2", "gaming-pc": "gaming:9000"}
    d = remote.Device("x", "http://10.0.0.5:9000/", "t")
    assert (d.host, d.port) == ("10.0.0.5", 9000)
    d = remote.Device("x", "laptop", "t")
    assert (d.host, d.port) == ("laptop", 8765)


def test_remote_calls():
    server, ran = _setup()
    try:
        names = set(tools.TOOLS)
        assert {"laptop__open_app", "laptop__say", "laptop___r_echo", "copy_file", "send_clipboard"} <= names
        assert "laptop__copy_file" not in names  # hub tools aren't re-exported

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
        assert tools.run("laptop___r_maybe", {"x": "play"}, confirm=None)[0]

        ok, out = tools.run("laptop___r_img", {})
        assert ok and isinstance(out, registry.Image) and out.png_b64 == "aGk=" and "laptop" in out.caption

        # the server refuses unconfirmed confirm-tier calls even from a client that skips asking
        assert "needs_confirm" in remote.DEVICES["laptop"].request("POST", "/call", {"name": "_r_danger", "args": {}})

        bad = remote.Device("laptop", f"127.0.0.1:{server.server_address[1]}", "nope")
        try:
            bad.request("GET", "/tools")
            raise AssertionError("bad token accepted")
        except registry.ToolError as e:
            assert "token" in str(e)

        # keep-alive + TCP_NODELAY: many calls stay fast
        start = time.perf_counter()
        for i in range(50):
            assert tools.run("laptop___r_echo", {"msg": str(i)})[0]
        assert (time.perf_counter() - start) / 50 < 0.02  # catches the ~40 ms Nagle delay
    finally:
        _teardown(server)


def test_feed_pushes_messages_status_and_tool_changes():
    heard = []
    old = registry.announce
    registry.announce = heard.append
    state = {"window": '"Report.docx - Word" (WINWORD.EXE)', "in_use": True}
    server, _ = _setup(status=lambda: dict(state))
    try:
        # a timer set from here, running on the laptop, reminds us here
        ok, out = tools.run("laptop__set_timer", {"seconds": 1, "message": "stretch"})
        assert ok, out
        assert _wait(lambda: heard == ["From laptop: Reminder: stretch"]), heard

        # the laptop's active window reaches us without a request
        assert _wait(lambda: "Report.docx" in remote.context_line()), remote.context_line()
        assert "laptop: in use" in remote.context_line()
        state.update(window='"Spotify" (Spotify.exe)', in_use=False)
        assert _wait(lambda: "laptop: idle, active window \"Spotify\"" in remote.context_line())

        # new tools on the laptop show up here
        registry.tool("_r_new", "new", risk="read")(lambda: "new!")
        remote.FEED.poke()
        assert _wait(lambda: "laptop___r_new" in tools.TOOLS)
        assert tools.run("laptop___r_new", {}) == (True, "new!")
    finally:
        registry.announce = old
        _teardown(server)


def test_offline_fails_fast():
    port = _free_port()  # nothing listens here
    dev = remote.Device("ghost", f"127.0.0.1:{port}", TOKEN)
    for expect in ("Can't reach", "offline"):
        start = time.perf_counter()
        try:
            dev.call("open_app", {"name": "x"}, None)
            raise AssertionError("call to an offline PC succeeded")
        except registry.ToolError as e:
            assert expect in str(e), e
        assert time.perf_counter() - start < 2.5


def test_copy_file_streams(tmp_path):
    server, _ = _setup()
    try:
        data = os.urandom(3 * 1024 * 1024 + 17)
        src = tmp_path / "build.bin"
        src.write_bytes(data)
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        # laptop -> here (download) and here -> laptop (upload), into a folder and to a file name
        ok, out = tools.run("copy_file", {"from_pc": "laptop", "from_path": str(src),
                                          "to_pc": "here", "to_path": str(out_dir)}, confirm=lambda r: True)
        assert ok, out
        assert (out_dir / "build.bin").read_bytes() == data and "3.1 MB" in out
        ok, out = tools.run("copy_file", {"from_pc": "desktop", "from_path": str(src),
                                          "to_pc": "the laptop", "to_path": str(out_dir / "up.bin")},
                            confirm=lambda r: True)
        assert ok, out
        assert (out_dir / "up.bin").read_bytes() == data
        assert not list(out_dir.glob("*.part"))

        ok, out = tools.run("copy_file", {"from_pc": "laptop", "from_path": str(tmp_path / "missing"),
                                          "to_pc": "here", "to_path": str(out_dir)}, confirm=lambda r: True)
        assert not ok and "No such file" in out
        ok, out = tools.run("copy_file", {"from_pc": "computer nine", "from_path": str(src),
                                          "to_pc": "here", "to_path": str(out_dir)}, confirm=lambda r: True)
        assert not ok and "Unknown PC" in out

        # uploads must carry the user's approval
        dev = remote.DEVICES["laptop"]
        link = remote._Link(dev.host, dev.port)
        resp = link.send("PUT", "/file?path=" + str(out_dir / "sneaky"), b"x", dev._headers({"Content-Length": "1"}))
        assert resp.status == 403 and not (out_dir / "sneaky").exists()
        link.close()
    finally:
        _teardown(server)


def test_discovery():
    port = _free_port(socket.SOCK_DGRAM)
    discovery.Responder("laptop", 8765, TOKEN, port=port, log=lambda s: None)
    found = discovery.probe(TOKEN, targets=[("127.0.0.1", port)], timeout=0.5)
    assert found == [("laptop", "127.0.0.1", 8765)]
    assert discovery.probe("someone-elses-token-123", targets=[("127.0.0.1", port)], timeout=0.3) == []


def test_split_target_and_routes():
    remote.here = "desktop"
    _clear()
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
        assert remote.split_target("say hi from computer one on the laptop") == ("laptop", "say hi from computer one")
        assert remote.split_target("open the photos from laptop on computer 2") == ("computer-2", "open the photos from laptop")
        pc, rest = remote.split_target("say dinner is ready on the laptop")
        assert (pc, router.route(rest)) == ("laptop", ("say", {"text": "dinner is ready"}))
        assert router.route("send my clipboard to the laptop") == ("send_clipboard", {"to_pc": "the laptop"})
    finally:
        _clear()


def test_brain_runs_pcs_in_parallel():
    from jarvis import brain as brain_mod

    brain_mod.active_window = lambda: None
    remote.here = "desktop"

    def slow(tag):
        def fn(args, confirm):
            time.sleep(0.3)
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
