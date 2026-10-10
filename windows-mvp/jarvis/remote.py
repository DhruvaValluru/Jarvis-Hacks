"""Talk to Jarvis on other PCs.

Every PC can run a small server (python -m jarvis --serve, or JARVIS_SERVE=1) that
exposes its own tools over HTTP. The PC you talk to connects to the PCs listed in
JARVIS_DEVICES and registers their tools as "<device>__<tool>", so Claude (and the
fast path) can act on any PC in one plan: read on one, write or run on another.

Speed: one kept-alive connection per device (no reconnect per call), tool lists
fetched once in parallel, and calls to different PCs in the same step run at once.

Security: every request needs the shared JARVIS_TOKEN. Run it over Tailscale (or a
trusted LAN), never on a public IP. Consequential calls are confirmed on the PC
you're talking to; the server refuses them unless the caller says they were confirmed.
"""

import base64
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
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from . import config
from .tools import registry
from .tools.registry import Image, Tool, ToolError

SEP = "__"  # laptop__open_app
DEFAULT_PORT = 8765
MAX_BODY = 100 * 1024 * 1024
RETRY_EVERY = 20  # seconds between reconnect attempts to offline PCs

DEVICES: dict[str, "Device"] = {}
here = "this-pc"  # this PC's name, set by connect()


device_name = config._pc_name  # 'Gaming PC' -> 'gaming-pc'


def device_of(tool_name: str) -> str:
    """Which PC a tool runs on: 'laptop__open_app' -> 'laptop', 'open_app' -> here."""
    return tool_name.split(SEP, 1)[0] if SEP in tool_name else here


# ---------------------------------------------------------------- client


class Device:
    """One remote PC, reached over a single kept-alive HTTP connection."""

    def __init__(self, name: str, address: str, token: str):
        self.name = name
        address = re.sub(r"^https?://", "", address).rstrip("/")
        host, _, port = address.rpartition(":") if re.search(r":\d+$", address) else (address, "", "")
        self.host, self.port = host, int(port or DEFAULT_PORT)
        self.token = token
        self.online = False
        self._conn: http.client.HTTPConnection | None = None
        self._lock = threading.Lock()

    def request(self, method: str, path: str, body: dict | None = None, timeout: float = 150) -> dict:
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}
        with self._lock:
            for attempt in (1, 2):
                reused = self._conn is not None
                try:
                    if self._conn is None:
                        self._conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
                        self._conn.connect()
                        # Send small requests immediately instead of waiting ~40 ms (Nagle).
                        self._conn.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    conn = self._conn
                    conn.sock.settimeout(timeout)
                    conn.request(method, path, body=data, headers=headers)
                    resp = conn.getresponse()
                    raw = resp.read()
                    break
                except (http.client.RemoteDisconnected, BrokenPipeError, ConnectionResetError) as e:
                    # A kept-alive connection the other side dropped: reconnect once.
                    self._close()
                    if not (reused and attempt == 1):
                        raise ToolError(f"{self.name} dropped the connection ({e}).") from e
                except (OSError, http.client.HTTPException) as e:
                    self._close()
                    self.online = False
                    raise ToolError(f"Can't reach {self.name} at {self.host}:{self.port} ({type(e).__name__}).") from e
        if resp.status == 401:
            raise ToolError(f"{self.name} rejected the token. JARVIS_TOKEN must match on both PCs.")
        if resp.status != 200:
            raise ToolError(f"{self.name} returned HTTP {resp.status}: {raw[:200].decode(errors='replace')}")
        self.online = True
        return json.loads(raw)

    def _close(self) -> None:
        if self._conn:
            self._conn.close()
        self._conn = None

    def call(self, tool: str, args: dict, confirm: Callable[[str], bool] | None, confirmed: bool = False):
        """Run a tool on this PC. Asks the user here if the remote side wants confirmation."""
        out = self.request("POST", "/call", {"name": tool, "args": args, "confirmed": confirmed})
        if "needs_confirm" in out:
            if not (confirm and confirm(f"on {self.name}: {out['needs_confirm']}")):
                return False, "The user declined this action. Do not retry it; ask what they want instead."
            out = self.request("POST", "/call", {"name": tool, "args": args, "confirmed": True})
        if "image" in out:
            return out["ok"], Image(out["image"], f"[{self.name}] {out.get('caption', '')}")
        return out["ok"], out["text"]

    def fetch_tools(self) -> int:
        """Register this PC's tools locally as '<name>__<tool>'. Returns how many."""
        schemas = self.request("GET", "/tools", timeout=3)
        for s in schemas:
            register_proxy(self, s)
        return len(schemas)


def register_proxy(dev: Device, schema: dict) -> None:
    remote_name = schema["name"]
    risk = schema.get("risk", "act")

    def proxy(args: dict, confirm):
        # A "confirm" tool was already approved here by registry.run before we got called.
        ok, out = dev.call(remote_name, args, confirm, confirmed=risk == "confirm")
        if not ok:
            raise ToolError(str(out))
        return out

    inp = schema.get("input_schema", {})
    registry.TOOLS[f"{dev.name}{SEP}{remote_name}"] = Tool(
        name=f"{dev.name}{SEP}{remote_name}",
        description=f"[Runs on {dev.name}] {schema.get('description', '')}",
        func=proxy,
        params=inp.get("properties", {}),
        required=tuple(inp.get("required", ())),
        risk=risk,
        remote=True,
    )


def connect(cfg: config.Config, log: Callable[[str], None] = print) -> None:
    """Connect to every PC in JARVIS_DEVICES (in parallel), then keep retrying offline ones."""
    global here
    here = cfg.name
    for name, address in cfg.devices.items():
        if name != here:
            DEVICES[name] = Device(name, address, cfg.token)
    if not DEVICES:
        return
    if not cfg.token:
        log("JARVIS_DEVICES is set but JARVIS_TOKEN isn't; other PCs will refuse. Copy the token from the other PC's --serve window.")
    _register_copy_file()

    def attempt(dev: Device, quiet: bool) -> None:
        try:
            n = dev.fetch_tools()
            log(f"Connected to {dev.name} ({n} tools).")
        except ToolError as e:
            if not quiet:
                log(f"{dev.name} is offline for now ({e}). I'll keep trying.")

    with ThreadPoolExecutor(len(DEVICES)) as pool:
        list(pool.map(lambda d: attempt(d, False), DEVICES.values()))

    def retry_loop() -> None:
        while True:
            time.sleep(RETRY_EVERY)
            for dev in DEVICES.values():
                if not dev.online:
                    attempt(dev, True)

    threading.Thread(target=retry_loop, daemon=True, name="jarvis-reconnect").start()


def prompt_note() -> str:
    """Extra system-prompt text describing the other PCs (empty when there are none)."""
    if not DEVICES:
        return ""
    others = ", ".join(f"{d.name} ({'online' if d.online else 'offline'})" for d in DEVICES.values())
    return (
        f"\n\nMultiple PCs: you are running on \"{here}\". Other PCs: {others}. Tools named "
        f"<pc>{SEP}<tool> run on that PC; tools without a prefix run on {here}. When the user names a PC "
        "(\"on my laptop\", \"computer two\"), use that PC's tools; otherwise use this one. You can chain PCs: "
        "read or build something on one, then pass the result to a tool on another. Move files between PCs "
        "with copy_file, then open or run them there. Tool calls to different PCs in the same step run in parallel."
    )


def resolve(spoken: str) -> str | None:
    """Match a spoken PC name ('the laptop', 'computer 2', 'Computer Two') to a device or this PC."""
    s = device_name(spoken)
    s = re.sub(r"^(the|my)-", "", s)
    names = [here, *DEVICES]
    words = {"one": "1", "two": "2", "three": "3", "four": "4", "won": "1", "to": "2", "too": "2"}
    def norm(n: str) -> str:
        return "".join(words.get(p, p) for p in n.split("-"))
    for n in names:
        if s == n or norm(s) == norm(n):
            return n
    if s in ("here", "this-pc", "this-computer", "this-one"):
        return here
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
    if m := re.fullmatch(r"(.+?),? (?:on|at|from) (.{1,30})", t, re.I):
        if pc := resolve(m.group(2)):
            return pc, m.group(1)
    if m := re.fullmatch(r"(?:on|at|from) (.+)", t, re.I):
        words = m.group(1).split()
        for n in (1, 2, 3):
            if len(words) > n and (pc := resolve(" ".join(words[:n]).rstrip(",:"))):
                return pc, " ".join(words[n:])
    return None, text


# ------------------------------------------------------- file transfer


@registry.tool("_file_get", "Read a file as base64 (used by copy_file).", {"path": {"type": "string"}}, ["path"], risk="read", hidden=True)
def _file_get(path: str) -> str:
    p = Path(path).expanduser()
    if not p.is_file():
        raise ToolError(f"No such file: {p}")
    if p.stat().st_size > 64 * 1024 * 1024:
        raise ToolError("File is over 64 MB; too big to copy this way.")
    return json.dumps({"name": p.name, "data": base64.b64encode(p.read_bytes()).decode("ascii")})


@registry.tool(
    "_file_put",
    "Write a base64 file (used by copy_file).",
    {"path": {"type": "string"}, "name": {"type": "string"}, "data": {"type": "string"}},
    ["path", "name", "data"],
    risk="confirm",
    hidden=True,
)
def _file_put(path: str, name: str, data: str) -> str:
    p = Path(path).expanduser()
    if p.is_dir() or path.endswith(("/", "\\")):
        p = p / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(base64.b64decode(data))
    return f"Saved {p} ({p.stat().st_size:,} bytes)."


def _register_copy_file() -> None:
    @registry.tool(
        "copy_file",
        "Copy a file from one PC to another (or the same PC). from_pc/to_pc are PC names; "
        f"'{here}' is this PC. to_path can be a folder. Then open or run it on the target PC with its tools.",
        {
            "from_pc": {"type": "string"},
            "from_path": {"type": "string"},
            "to_pc": {"type": "string"},
            "to_path": {"type": "string", "description": "Destination file or folder, e.g. C:\\Users\\me\\Downloads\\"},
        },
        ["from_pc", "from_path", "to_pc", "to_path"],
        risk="confirm",
    )
    def copy_file(from_pc: str, from_path: str, to_pc: str, to_path: str) -> str:
        src, dst = resolve(from_pc), resolve(to_pc)
        for spoken, got in ((from_pc, src), (to_pc, dst)):
            if got is None:
                raise ToolError(f"Unknown PC '{spoken}'. Known: {', '.join([here, *DEVICES])}.")
        packed = _on(src, "_file_get", {"path": from_path})
        f = json.loads(packed)
        return _on(dst, "_file_put", {"path": to_path, "name": f["name"], "data": f["data"]})


def _on(pc: str, tool: str, args: dict) -> str:
    """Run a tool (already approved by the user) on a PC by name."""
    if pc == here:
        ok, out = registry.run(tool, args, confirm=lambda r: True)
    else:
        ok, out = DEVICES[pc].call(tool, args, None, confirmed=True)
    if not ok:
        raise ToolError(f"{pc}: {out}")
    return str(out)


# ---------------------------------------------------------------- server


def _local_tools() -> list[Tool]:
    return [t for t in registry.TOOLS.values() if not t.remote and t.name != "copy_file"]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep connections alive between calls
    token = ""
    log: Callable[[str], None] = print

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

    def _authorized(self) -> bool:
        got = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if self.token and hmac.compare_digest(got.encode(), self.token.encode()):
            return True
        length = int(self.headers.get("Content-Length") or 0)
        if 0 < length <= MAX_BODY:
            self.rfile.read(length)  # drain so the connection stays usable
        self._send(401, {"error": "bad token"})
        return False

    def do_GET(self) -> None:
        if not self._authorized():
            return
        if self.path == "/tools":
            self._send(200, [{**t.schema(), "risk": t.risk} for t in _local_tools() if not t.hidden])
        elif self.path == "/ping":
            self._send(200, {"name": here})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        if not self._authorized():
            return
        length = int(self.headers.get("Content-Length") or 0)
        if self.path != "/call" or not 0 < length <= MAX_BODY:
            self._send(404 if self.path != "/call" else 413, {"error": "bad request"})
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
        if not t.hidden:
            self.log(f"  [from {self.client_address[0]}] {name} {registry._short(args, 120)}")
        ok, out = registry.run(name, args, confirm=lambda r: True)
        if isinstance(out, Image):
            self._send(200, {"ok": ok, "image": out.png_b64, "caption": out.caption})
        else:
            self._send(200, {"ok": ok, "text": str(out)})


def serve(cfg: config.Config, log: Callable[[str], None] = print, background: bool = False):
    """Serve this PC's tools to the other PCs. Blocks unless background=True."""
    global here
    here = cfg.name
    token = cfg.token or ensure_token()
    handler = type("Handler", (_Handler,), {"token": token, "log": staticmethod(log)})
    server = ThreadingHTTPServer((cfg.serve_host, cfg.port), handler)
    server.daemon_threads = True
    addr = f"{my_address()}:{cfg.port}"
    log(f"Serving {here}'s tools on {addr}. On the other PC, add to windows-mvp\\.env:")
    log(f"  JARVIS_DEVICES={here}={addr}")
    log(f"  JARVIS_TOKEN={token}")
    if background:
        threading.Thread(target=server.serve_forever, daemon=True, name="jarvis-server").start()
        return server
    server.serve_forever()


def ensure_token() -> str:
    """Create JARVIS_TOKEN in .env if it's missing, and return it."""
    token = secrets.token_urlsafe(18)
    env = config.MVP_DIR / ".env"
    text = env.read_text(encoding="utf-8") if env.is_file() else ""
    if re.search(r"^JARVIS_TOKEN=", text, re.M):
        text = re.sub(r"^JARVIS_TOKEN=.*$", f"JARVIS_TOKEN={token}", text, flags=re.M)
    else:
        text = text.rstrip("\n") + ("\n" if text else "") + f"JARVIS_TOKEN={token}\n"
    env.write_text(text, encoding="utf-8")
    os.environ["JARVIS_TOKEN"] = token
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
