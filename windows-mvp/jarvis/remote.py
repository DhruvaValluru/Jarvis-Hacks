"""Talk to Jarvis on your other PCs.

Every PC can serve its tools (python -m jarvis --serve, or JARVIS_SERVE=1). The PC
you talk to finds the others by itself (same JARVIS_TOKEN, see discovery.py) or uses
JARVIS_DEVICES, and registers their tools as "<pc>__<tool>". Claude and the fast path
can then act on any PC: build on one, copy the result, run it on another.

Two connections per PC, both kept alive:
  calls   request/response for tool calls (TCP_NODELAY: under 1 ms of overhead)
  feed    a long poll the other PC answers the moment something happens: a message
          for you (a timer you set there, "say ..."), a change of its active window,
          or a change of its tool list. It doubles as the heartbeat, so an offline PC
          is noticed right away and calls to it fail at once instead of hanging.
Files stream as raw bytes on their own connection, so a big copy never blocks calls.

Security: every request needs the shared JARVIS_TOKEN. Use Tailscale or a trusted
LAN, never a public IP. Consequential calls are confirmed on the PC you talk to; the
serving PC refuses them unless the caller says they were confirmed.
"""

import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import socket
import subprocess
import threading
import time
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable, Iterator

from . import config, discovery
from .tools import registry
from .tools.registry import Image, Tool, ToolError

SEP = "__"  # laptop__open_app
DEFAULT_PORT = 8765
MAX_JSON = 32 * 1024 * 1024
CHUNK = 1024 * 1024
CONNECT_TIMEOUT = 2  # seconds; a PC that doesn't answer by then is offline
FAIL_FAST = 5  # after a failure, calls fail at once for this long instead of waiting again
POLL_WAIT = 20  # seconds the other PC may hold a feed request open
DISCOVER_EVERY = 15

DEVICES: dict[str, "Device"] = {}
here = "this-pc"  # this PC's name
log: Callable[[str], None] = print
_devices_lock = threading.Lock()

device_name = config._pc_name  # 'Gaming PC' -> 'gaming-pc'


def device_of(tool_name: str) -> str:
    """Which PC a tool runs on: 'laptop__open_app' -> 'laptop', 'open_app' -> here."""
    return tool_name.split(SEP, 1)[0] if SEP in tool_name else here


# ---------------------------------------------------------------- client


class _Link:
    """One kept-alive HTTP connection. Not thread-safe: one user at a time."""

    def __init__(self, host: str, port: int):
        self.host, self.port = host, port
        self.conn: http.client.HTTPConnection | None = None

    def close(self) -> None:
        if self.conn:
            self.conn.close()
        self.conn = None

    def send(self, method: str, path: str, body=None, headers: dict | None = None, timeout: float = 150):
        """Send a request and return the response (caller reads it). Reconnects once if a
        kept-alive connection had gone stale; streamed bodies are never resent."""
        for attempt in (1, 2):
            reused = self.conn is not None
            try:
                if self.conn is None:
                    self.conn = http.client.HTTPConnection(self.host, self.port, timeout=CONNECT_TIMEOUT)
                    self.conn.connect()
                    # Send small requests immediately instead of waiting ~40 ms (Nagle).
                    self.conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.conn.sock.settimeout(timeout)
                self.conn.request(method, path, body=body, headers=headers or {})
                return self.conn.getresponse()
            except (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                self.close()
                if not (reused and attempt == 1 and (body is None or isinstance(body, bytes))):
                    raise
            except BaseException:
                self.close()
                raise
        raise AssertionError("unreachable")


class Device:
    """Another PC running Jarvis."""

    def __init__(self, name: str, address: str, token: str):
        self.name = name
        self.token = token
        self.set_address(address)
        self.online = False
        self.status: dict = {}  # live, from the feed: {"window": ..., "in_use": ...}
        self.tools_version = None
        self.rtt_ms: float | None = None
        self.ready = threading.Event()  # set after the first contact attempt
        self._down_until = 0.0
        self._lock = threading.Lock()
        self._stopped = False

    def stop(self) -> None:
        """Stop the feed (the device is being forgotten)."""
        self._stopped = True

    def set_address(self, address: str) -> None:
        address = re.sub(r"^https?://", "", address).rstrip("/")
        m = re.fullmatch(r"(.+):(\d+)", address)
        self.host, self.port = (m.group(1), int(m.group(2))) if m else (address, DEFAULT_PORT)
        self._link = _Link(self.host, self.port)

    @property
    def address(self) -> str:
        return f"{self.host}:{self.port}"

    def _headers(self, extra: dict | None = None) -> dict:
        return {"Authorization": f"Bearer {self.token}", **(extra or {})}

    def _check_up(self) -> None:
        if not self.online and time.monotonic() < self._down_until:
            raise ToolError(f"{self.name} is offline right now. Is Jarvis running there?")

    def _down(self) -> None:
        self.online = False
        self._down_until = time.monotonic() + FAIL_FAST

    def _parse(self, status: int, raw: bytes) -> dict:
        if status == 401:
            raise ToolError(f"{self.name} rejected the token. JARVIS_TOKEN must be the same on every PC.")
        try:
            out = json.loads(raw) if raw else {}
        except ValueError:
            out = {"error": raw[:200].decode(errors="replace")}
        if status != 200:
            raise ToolError(f"{self.name}: {out.get('error', f'HTTP {status}')}")
        return out

    def request(self, method: str, path: str, body: dict | None = None, timeout: float = 150) -> dict:
        self._check_up()
        data = json.dumps(body).encode() if body is not None else None
        headers = self._headers({"Content-Type": "application/json"} if data else None)
        with self._lock:
            try:
                resp = self._link.send(method, path, data, headers, timeout)
                raw = resp.read()
            except (OSError, http.client.HTTPException) as e:
                self._down()
                raise ToolError(f"Can't reach {self.name} at {self.address} ({type(e).__name__}).") from e
        self._down_until = 0.0
        return self._parse(resp.status, raw)

    def call(self, tool: str, args: dict, confirm: Callable[[str], bool] | None, confirmed: bool = False):
        """Run a tool there. Asks the user here if that PC wants the call confirmed."""
        out = self.request("POST", "/call", {"name": tool, "args": args, "confirmed": confirmed})
        if "needs_confirm" in out:
            if not (confirm and confirm(f"on {self.name}: {out['needs_confirm']}")):
                return False, "The user declined this action. Do not retry it; ask what they want instead."
            out = self.request("POST", "/call", {"name": tool, "args": args, "confirmed": True})
        if "image" in out:
            return out["ok"], Image(out["image"], f"[{self.name}] {out.get('caption', '')}")
        return out["ok"], out["text"]

    def fetch_tools(self) -> int:
        """(Re)register this PC's tools here as '<name>__<tool>'. Returns how many."""
        start = time.perf_counter()
        out = self.request("GET", "/tools", timeout=5)
        self.rtt_ms = (time.perf_counter() - start) * 1000
        prefix = f"{self.name}{SEP}"
        fresh = {f"{prefix}{s['name']}": _proxy(self, s) for s in out["tools"]}
        for stale in [n for n, t in list(registry.TOOLS.items()) if t.remote and n.startswith(prefix) and n not in fresh]:
            registry.TOOLS.pop(stale, None)
        registry.TOOLS.update(fresh)
        self.tools_version = out["version"]
        return len(fresh)

    def watch(self, on_event: Callable[[str, str], None]) -> None:
        """Keep the feed open forever: messages, live status, tool changes, heartbeat."""
        link = _Link(self.host, self.port)
        after: int | None = None
        backoff = 0.5
        while not self._stopped:
            if link.host != self.host or link.port != self.port:  # address changed (rediscovered)
                link.close()
                link = _Link(self.host, self.port)
            try:
                query = f"/events?wait={POLL_WAIT}" + (f"&after={after}" if after is not None else "")
                resp = link.send("GET", query, None, self._headers(), timeout=POLL_WAIT + 10)
                feed = self._parse(resp.status, resp.read())
                if not self.online or feed["tools_version"] != self.tools_version:
                    self._down_until = 0.0
                    n = self.fetch_tools()
                    if not self.online:
                        log(f"Connected to {self.name} at {self.address} ({n} tools, {self.rtt_ms:.0f} ms).")
                    self.online = True
            except (OSError, http.client.HTTPException, ToolError, KeyError, ValueError) as e:
                link.close()
                if self.online or not self.ready.is_set():
                    log(f"{self.name} is offline ({e}). I'll reconnect when it's back.")
                self._down()
                self.ready.set()
                time.sleep(backoff)
                backoff = min(backoff * 2, 5)
                continue
            backoff = 0.5
            self._down_until = 0.0
            self.ready.set()
            self.status = feed.get("status") or {}
            if after is not None and not self._stopped:
                for text in feed.get("events", []):
                    on_event(self.name, text)
            after = feed["seq"]
        link.close()


def _proxy(dev: Device, schema: dict) -> Tool:
    remote_name = schema["name"]
    risk = schema.get("risk", "act")

    def proxy(args: dict, confirm):
        # A "confirm" tool was already approved here by registry.run before we got called.
        ok, out = dev.call(remote_name, args, confirm, confirmed=risk == "confirm")
        if not ok:
            raise ToolError(str(out))
        return out

    inp = schema.get("input_schema", {})
    return Tool(
        name=f"{dev.name}{SEP}{remote_name}",
        description=f"[Runs on {dev.name}] {schema.get('description', '')}",
        func=proxy,
        params=inp.get("properties", {}),
        required=tuple(inp.get("required", ())),
        risk=risk,
        remote=True,
    )


def _announce_from(pc: str, text: str) -> None:
    registry.announce(f"From {pc}: {text}")


def add_device(name: str, address: str, token: str) -> "Device | None":
    """Start talking to a PC (no-op if we already know it at that address)."""
    name = device_name(name)
    if name == here:
        return None
    with _devices_lock:
        dev = DEVICES.get(name)
        if dev:
            if dev.address != address and not dev.online:
                dev.set_address(address)  # it moved (new IP); the feed picks this up
            return dev
        dev = DEVICES[name] = Device(name, address, token)
    _register_hub_tools()
    threading.Thread(target=dev.watch, args=(_announce_from,), daemon=True, name=f"jarvis-feed-{name}").start()
    return dev


def connect(cfg: config.Config, log_fn: Callable[[str], None] = print) -> None:
    """Connect to the PCs in JARVIS_DEVICES and any found on the network. Waits up to ~2 s
    so the first command can already use them; the rest happens in the background."""
    global here, log
    here, log = cfg.name, log_fn
    if not cfg.token:
        if cfg.devices:
            log("JARVIS_DEVICES is set but JARVIS_TOKEN isn't. Copy the token from the other PC's --serve window.")
        return
    for name, address in cfg.devices.items():
        add_device(name, address, cfg.token)
    if cfg.discover:
        found = threading.Event()

        def discover_loop() -> None:
            while True:
                try:
                    for name, ip, port in discovery.probe(cfg.token):
                        add_device(name, f"{ip}:{port}", cfg.token)
                except OSError:
                    pass
                found.set()
                time.sleep(DISCOVER_EVERY)

        threading.Thread(target=discover_loop, daemon=True, name="jarvis-discover").start()
        found.wait(1.5)
    deadline = time.monotonic() + 2.5
    for dev in list(DEVICES.values()):
        dev.ready.wait(max(0.0, deadline - time.monotonic()))


def prompt_note() -> str:
    """Extra system-prompt text once other PCs exist (kept stable so the prompt cache holds)."""
    if not DEVICES:
        return ""
    return (
        f"\n\nMultiple PCs: you run on \"{here}\". Tools named <pc>{SEP}<tool> run on that PC; tools without a "
        f"prefix run on {here}. Each request starts with which PCs are online and what they're showing. When the "
        "user names a PC (\"on my laptop\", \"computer two\"), use that PC's tools; otherwise this one. Chain PCs "
        "freely: read or build something on one, pass the result to a tool on another. Move files with copy_file, "
        "text with send_clipboard, and speak on another PC with <pc>__say. Calls to different PCs in the same "
        "step run in parallel, so batch independent calls."
    )


def context_line() -> str:
    """Live state of the other PCs for the start of each request (no network call: it's pushed to us)."""
    parts = []
    for d in list(DEVICES.values()):
        if not d.online:
            parts.append(f"{d.name}: offline")
            continue
        bits = ["in use" if d.status.get("in_use") else "idle"]
        if d.status.get("window"):
            bits.append(f"active window {d.status['window']}")
        parts.append(f"{d.name}: {', '.join(bits)}")
    return "; ".join(parts)


def resolve(spoken: str) -> str | None:
    """Match a spoken PC name ('the laptop', 'computer 2', 'Computer Two') to a PC."""
    s = re.sub(r"^(the|my)-", "", device_name(spoken))
    if s in ("here", "this-pc", "this-computer", "this-one", "me"):
        return here
    words = {"one": "1", "two": "2", "three": "3", "four": "4", "won": "1", "to": "2", "too": "2"}

    def norm(n: str) -> str:
        return "".join(words.get(p, p) for p in n.split("-"))

    for n in [here, *list(DEVICES)]:
        if s == n or norm(s) == norm(n):
            return n
    return None


def split_target(text: str) -> tuple[str | None, str]:
    """Pull a PC name out of a command for the fast path.

    'open spotify on the laptop' / 'on laptop, open spotify' / 'tell computer 2 to pause'
    -> ('laptop' or 'computer-2', 'open spotify'). Unknown names leave the text alone.
    """
    t = text.strip(" .!?")
    if m := re.fullmatch(r"(?:tell|ask|have) (.+?) to (.+)", t, re.I):
        if pc := resolve(m.group(1)):
            return pc, m.group(2)
    for m in reversed(list(re.finditer(r",? (?:on|at|from) ", t, re.I))):  # rightmost "on <pc>" wins
        if m.start() > 0 and (pc := resolve(t[m.end():])):
            return pc, t[: m.start()]
    if m := re.fullmatch(r"(?:on|at|from) (.+)", t, re.I):
        words = m.group(1).split()
        for n in (1, 2, 3):
            if len(words) > n and (pc := resolve(" ".join(words[:n]).rstrip(",:"))):
                return pc, " ".join(words[n:])
    return None, text


# ------------------------------------------------- tools that span PCs


def _pc(spoken: str) -> str:
    pc = resolve(spoken)
    if pc is None:
        raise ToolError(f"Unknown PC '{spoken}'. Known: {', '.join([here, *list(DEVICES)])}.")
    return pc


def _on(pc: str, tool: str, args: dict) -> str:
    """Run a tool the user already approved, on a PC by name."""
    if pc == here:
        ok, out = registry.run(tool, args, confirm=lambda r: True)
    else:
        ok, out = DEVICES[pc].call(tool, args, None, confirmed=True)
    if not ok:
        raise ToolError(f"{pc}: {out}")
    return str(out)


def _chunks(f) -> Iterator[bytes]:
    return iter(lambda: f.read(CHUNK), b"")


def _save(path: str, name: str, size: int, chunks) -> Path:
    """Write streamed bytes to a file (or into a folder), atomically."""
    p = Path(os.path.expandvars(path)).expanduser()
    if p.is_dir() or path.endswith(("/", "\\")):
        p = p / Path(name).name  # never trust a path in the name
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".part")
    got = 0
    try:
        with open(tmp, "wb") as f:
            for c in chunks:
                f.write(c)
                got += len(c)
        if got != size:
            raise ToolError(f"The transfer was cut off ({got:,} of {size:,} bytes).")
        os.replace(tmp, p)
    finally:
        if tmp.exists():
            tmp.unlink()
    return p


def _source(pc: str, path: str):
    """Open a file on a PC for streaming: (name, size, chunks, close)."""
    if pc == here:
        p = Path(os.path.expandvars(path)).expanduser()
        if not p.is_file():
            raise ToolError(f"No such file on {here}: {p}")
        f = open(p, "rb")
        return p.name, p.stat().st_size, _chunks(f), f.close
    dev = DEVICES[pc]
    dev._check_up()
    link = _Link(dev.host, dev.port)  # its own connection, so calls aren't blocked
    try:
        resp = link.send("GET", "/file?" + urllib.parse.urlencode({"path": path}), None, dev._headers(), 60)
        if resp.status != 200:
            dev._parse(resp.status, resp.read())
    except (OSError, http.client.HTTPException) as e:
        link.close()
        raise ToolError(f"Can't reach {pc} ({type(e).__name__}).") from e
    except ToolError:
        link.close()
        raise
    return resp.getheader("X-File-Name", "file"), int(resp.getheader("Content-Length", "0")), _chunks(resp), link.close


def _sink(pc: str, path: str, name: str, size: int, chunks) -> str:
    if pc == here:
        return str(_save(path, name, size, chunks))
    dev = DEVICES[pc]
    dev._check_up()
    link = _Link(dev.host, dev.port)
    try:
        resp = link.send(
            "PUT", "/file?" + urllib.parse.urlencode({"path": path, "name": name}), chunks,
            dev._headers({"Content-Length": str(size), "X-Jarvis-Confirmed": "1"}), 120,
        )
        return dev._parse(resp.status, resp.read())["saved"]
    except (OSError, http.client.HTTPException) as e:
        raise ToolError(f"Lost the connection to {pc} while sending ({type(e).__name__}).") from e
    finally:
        link.close()


def copy_file(from_pc: str, from_path: str, to_pc: str, to_path: str) -> str:
    src, dst = _pc(from_pc), _pc(to_pc)
    start = time.perf_counter()
    name, size, chunks, close = _source(src, from_path)
    try:
        saved = _sink(dst, to_path, name, size, chunks)
    finally:
        close()
    secs = time.perf_counter() - start
    return f"Copied {name} ({size / 1e6:.1f} MB) from {src} to {dst} as {saved} in {secs:.1f} s."


def send_clipboard(to_pc: str, from_pc: str = "") -> str:
    src, dst = _pc(from_pc or here), _pc(to_pc)
    text = _on(src, "clipboard_get", {})
    _on(dst, "clipboard_set", {"text": text})
    return f"Sent the clipboard from {src} to {dst} ({len(text)} characters)."


_hub_tools_done = False


def _register_hub_tools() -> None:
    global _hub_tools_done
    if _hub_tools_done:
        return
    _hub_tools_done = True
    registry.tool(
        "copy_file",
        "Copy a file from one PC to another (or within one). PC names as listed in the request context; "
        f"'here' is {here}. to_path can be a folder. Streams any size. Then open or run it there with that PC's tools.",
        {
            "from_pc": {"type": "string"},
            "from_path": {"type": "string"},
            "to_pc": {"type": "string"},
            "to_path": {"type": "string", "description": "Destination file or folder, e.g. %USERPROFILE%\\Downloads\\"},
        },
        ["from_pc", "from_path", "to_pc", "to_path"],
        risk="confirm",
    )(copy_file)
    registry.tool(
        "send_clipboard",
        "Copy the clipboard text from one PC (default: here) to another PC's clipboard.",
        {"to_pc": {"type": "string"}, "from_pc": {"type": "string"}},
        ["to_pc"],
    )(send_clipboard)


# ---------------------------------------------------------------- server


class _Feed:
    """What the other PCs long-poll: messages for them, plus this PC's live status."""

    def __init__(self):
        self.cond = threading.Condition()
        self.seq = int(time.time() * 1000)  # keeps increasing across restarts
        self.events: deque[tuple[int, str]] = deque(maxlen=200)
        self.status: dict = {}

    def push(self, text: str) -> None:
        with self.cond:
            self.seq += 1
            self.events.append((self.seq, text))
            self.cond.notify_all()

    def set_status(self, status: dict) -> None:
        with self.cond:
            if status != self.status:
                self.status = status
                self.seq += 1
                self.cond.notify_all()

    def poke(self) -> None:
        """Wake every waiting PC (e.g. this PC's tool list changed)."""
        with self.cond:
            self.seq += 1
            self.cond.notify_all()

    def wait(self, after: int | None, timeout: float) -> dict:
        with self.cond:
            if after is not None and after <= self.seq:
                self.cond.wait_for(lambda: self.seq > after, timeout)
            events = [t for s, t in self.events if after is not None and s > after]
            return {"seq": self.seq, "events": events, "status": self.status, "tools_version": tools_version()}


FEED = _Feed()


def _served_tools() -> list[Tool]:
    return [t for t in list(registry.TOOLS.values()) if not t.remote and t.name not in ("copy_file", "send_clipboard")]


def tools_version() -> str:
    names = "|".join(sorted(f"{t.name}:{t.risk}" for t in _served_tools() if not t.hidden))
    return hashlib.sha1(names.encode()).hexdigest()[:12]


def local_status() -> dict:
    """What the other PCs see about this one: active window and whether someone's using it."""
    from .tools import apps

    w = apps.active_window()
    idle = apps.idle_seconds()
    return {
        "window": f'"{w["title"][:80]}" ({w["process"]})' if w else "",
        "in_use": idle is not None and idle < 60,
    }


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep connections alive between calls
    token = ""

    def setup(self) -> None:
        super().setup()
        self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    def log_message(self, *a) -> None:  # silence per-request logging
        pass

    def _send(self, status: int, payload) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _length(self) -> int:
        try:
            return int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return -1

    def _authorized(self) -> bool:
        got = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if self.token and hmac.compare_digest(got.encode(), self.token.encode()):
            return True
        self.close_connection = True  # don't bother reading a body we won't use
        self._send(401, {"error": "bad token"})
        return False

    def _query(self) -> tuple[str, dict]:
        url = urllib.parse.urlsplit(self.path)
        return url.path, {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}

    def do_GET(self) -> None:
        if not self._authorized():
            return
        path, q = self._query()
        if path == "/events":
            after = int(q["after"]) if q.get("after", "").isdigit() else None
            try:
                wait = min(float(q.get("wait", POLL_WAIT)), 60)
            except ValueError:
                wait = POLL_WAIT
            self._send(200, FEED.wait(after, wait))
        elif path == "/tools":
            tools = [{**t.schema(), "risk": t.risk} for t in _served_tools() if not t.hidden]
            self._send(200, {"version": tools_version(), "tools": tools})
        elif path == "/file":
            self._get_file(q.get("path", ""))
        elif path == "/ping":
            self._send(200, {"name": here})
        else:
            self._send(404, {"error": "not found"})

    def _get_file(self, path: str) -> None:
        p = Path(os.path.expandvars(path)).expanduser()
        if not p.is_file():
            self._send(404, {"error": f"No such file on {here}: {p}"})
            return
        log(f"  [from {self.client_address[0]}] sending {p}")
        with open(p, "rb") as f:
            size = os.fstat(f.fileno()).st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.send_header("X-File-Name", p.name)
            self.end_headers()
            for c in _chunks(f):
                self.wfile.write(c)

    def do_PUT(self) -> None:
        if not self._authorized():
            return
        path, q = self._query()
        size = self._length()
        if path != "/file" or size < 0 or self.headers.get("X-Jarvis-Confirmed") != "1":
            self.close_connection = True
            self._send(403 if path == "/file" else 404, {"error": "file writes must be confirmed by the user"})
            return
        remaining = size

        def body() -> Iterator[bytes]:
            nonlocal remaining
            while remaining > 0:
                c = self.rfile.read(min(CHUNK, remaining))
                if not c:
                    return
                remaining -= len(c)
                yield c

        try:
            saved = _save(q.get("path", ""), q.get("name", "file"), size, body())
        except (OSError, ToolError) as e:
            self.close_connection = True  # the rest of the body is unread
            self._send(400, {"error": f"Couldn't save on {here}: {e}"})
            return
        log(f"  [from {self.client_address[0]}] saved {saved}")
        self._send(200, {"saved": str(saved)})

    def do_POST(self) -> None:
        if not self._authorized():
            return
        length = self._length()
        if self._query()[0] != "/call" or not 0 < length <= MAX_JSON:
            self.close_connection = True
            self._send(400, {"error": "bad request"})
            return
        try:
            body = json.loads(self.rfile.read(length))
            name, args, confirmed = body["name"], dict(body.get("args") or {}), bool(body.get("confirmed"))
        except (ValueError, KeyError, TypeError):
            self._send(400, {"error": "bad json"})
            return
        t = registry.TOOLS.get(name)
        if t is None or t.remote:
            self._send(200, {"ok": False, "text": f"Unknown tool on {here}: {name}"})
            return
        reason = t.confirmation_reason(args)
        if reason and not confirmed:
            self._send(200, {"needs_confirm": reason})
            return
        log(f"  [from {self.client_address[0]}] {name} {registry._short(args, 120)}")
        registry.reply_to(FEED.push)  # later messages from this call (timers) go back to the caller
        try:
            ok, out = registry.run(name, args, confirm=lambda r: True)
        finally:
            registry.reply_to(None)
        if isinstance(out, Image):
            self._send(200, {"ok": ok, "image": out.png_b64, "caption": out.caption})
        else:
            self._send(200, {"ok": ok, "text": str(out)})


def serve(cfg: config.Config, log_fn: Callable[[str], None] = print, background: bool = False,
          discovery_port: int = discovery.PORT, status_every: float = 1.0):
    """Serve this PC's tools to the other PCs. Blocks unless background=True."""
    global here, log
    here, log = cfg.name, log_fn
    token = cfg.token = cfg.token or ensure_token()  # connect() uses the same token
    if len(token) < 16:
        log("JARVIS_TOKEN is short; use at least 16 random characters (delete it and --serve makes one).")
    handler = type("Handler", (_Handler,), {"token": token})
    server = ThreadingHTTPServer((cfg.serve_host, cfg.port), handler)
    server.daemon_threads = True
    if cfg.discover:
        discovery.Responder(here, cfg.port, token, port=discovery_port, log=log)

    def watch_status() -> None:
        while True:
            try:
                FEED.set_status(local_status())
            except Exception:  # noqa: BLE001 - status is best-effort
                pass
            time.sleep(status_every)

    threading.Thread(target=watch_status, daemon=True, name="jarvis-status").start()
    addr = f"{my_address()}:{cfg.port}"
    log(f"Serving {here}'s tools on {addr}. On your other PCs, put this in {config.settings_location()}:")
    log(f"  JARVIS_TOKEN={token}")
    log("They find this PC by themselves on the same network or Tailscale. If one doesn't, also add:")
    log(f"  JARVIS_DEVICES={here}={addr}")
    if background:
        threading.Thread(target=server.serve_forever, daemon=True, name="jarvis-server").start()
        return server
    server.serve_forever()


def ensure_token() -> str:
    """Create JARVIS_TOKEN if it's missing (in .env from source, the secret store when installed); return it."""
    token = secrets.token_urlsafe(18)
    if config.frozen():
        from .settings import Store

        Store().set_secret("JARVIS_TOKEN", token)
        return token
    env = config.MVP_DIR / ".env"
    text = env.read_text(encoding="utf-8") if env.is_file() else ""
    if re.search(r"^JARVIS_TOKEN=", text, re.M):
        text = re.sub(r"^JARVIS_TOKEN=.*$", f"JARVIS_TOKEN={token}", text, flags=re.M)
    else:
        text = text.rstrip("\n") + ("\n" if text else "") + f"JARVIS_TOKEN={token}\n"
    env.write_text(text, encoding="utf-8")
    return token


def my_address() -> str:
    """This PC's Tailscale IP if Tailscale is running, else its LAN IP."""
    try:
        out = subprocess.run(
            ["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=3,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.split()[0]
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # no packet is sent; just picks the outgoing interface
            return s.getsockname()[0]
    except OSError:
        return socket.gethostname()
