"""The Alfred app's local server: the core API over AlfredService, the UI's files, and local-only sign-in.

Only this PC's Alfred window may drive Alfred. The server listens on 127.0.0.1 at a random port with
a random key per launch. The window first opens /auth?code=<one-time code>, which answers with the
page and the key in a <meta>. The page keeps the key in sessionStorage, which browsers keep per
origin (scheme, host and port), and sends it as the X-Alfred header on every /api call. Not a cookie:
browsers send 127.0.0.1's cookies to every port, so any local server the browser visits would get it.
Every request needs a Host of 127.0.0.1:<port> or localhost:<port> (a DNS-rebinding page sends its own
name); requests that change anything also need an Origin of that host. Another site's page can't send
the header (we never answer CORS preflights) or read the key. A second launch of the app asks this
one to show its window through /api/instance/show with the instance token from data_dir()/run (see main.py).

Routes: GET /api/health, /api/status, /api/events (SSE, ids = event seq), /api/settings,
/api/audit, /api/consent; POST /api/command, /api/stop, /api/approval, /api/reset, /api/restart,
/api/telegram, /api/consent, /api/quit; PUT /api/settings, /api/secrets/{name};
DELETE /api/secrets/{name}. Other features add theirs on App.router: the setup (/api/onboarding/*,
onboarding/routes.py) and voice mode (/api/voice/*, voice.py).
"""

import dataclasses
import hmac
import json
import re
import secrets
import threading
import time
import traceback
from pathlib import Path
from urllib.parse import unquote

from .. import __version__, config, consent, onboarding, settings
from ..service import NotReady, QueueFull
from ..service.bridge import KEYS as TELEGRAM_KEYS
from . import voice
from .http import HTTPError, Request, Response, Router, make_server, serve_in_thread

UI_DIR = Path(__file__).resolve().parent / "ui"
KEY_HEADER = "X-Alfred"
KEY_META = "alfred-key"
CODE_TTL = 300  # seconds a one-time sign-in link works
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
       "font-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP, "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY", "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}
TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".ico": "image/x-icon", ".json": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
}

# Plain settings the window may change, and what each accepts. API keys have their own write-only routes.
_FLAG = re.compile(r"[01]")
SETTINGS = {
    "JARVIS_MODEL": re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,99}"),
    "JARVIS_EFFORT": re.compile(r"low|medium|high"),
    "JARVIS_SPEAK": _FLAG,
    "JARVIS_COMPUTER_USE": _FLAG,
    "JARVIS_NAME": re.compile(r"[A-Za-z0-9][A-Za-z0-9 _-]{0,29}"),
    "JARVIS_WHISPER_MODEL": re.compile(r"[a-z0-9][a-z0-9.-]{0,39}"),
    "JARVIS_MAX_STEPS": re.compile(r"[1-9][0-9]?|1[0-9][0-9]|200"),
    "JARVIS_FALLBACKS": _FLAG,
    "TELEGRAM_OWNER_ID": re.compile(r"[0-9]{1,20}"),
    "OPENAI_LIVE_VOICE": re.compile(r"[a-z][a-z0-9_-]{0,39}"),
    "OPENAI_LIVE_MODEL": re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,99}"),
}
LABELS = {
    "JARVIS_MODEL": "Model", "JARVIS_EFFORT": "Effort (low, medium or high)", "JARVIS_SPEAK": "Spoken replies",
    "JARVIS_COMPUTER_USE": "Computer use", "JARVIS_NAME": "PC name (letters, digits, spaces, - and _)",
    "JARVIS_WHISPER_MODEL": "Speech model", "JARVIS_MAX_STEPS": "Max steps (1 to 200)",
    "JARVIS_FALLBACKS": "Fallbacks", "TELEGRAM_OWNER_ID": "Telegram owner ID (digits)",
    "OPENAI_LIVE_VOICE": "Voice mode voice", "OPENAI_LIVE_MODEL": "Voice mode model",
}
DEFAULTS = {
    "JARVIS_MODEL": "claude-opus-5-5", "JARVIS_EFFORT": "low", "JARVIS_SPEAK": "1", "JARVIS_COMPUTER_USE": "1",
    "JARVIS_WHISPER_MODEL": "base.en", "JARVIS_FALLBACKS": "1", **voice.DEFAULTS,
}
TELEGRAM_ONLY = frozenset(TELEGRAM_KEYS)  # only the Telegram bridge reads these; the engine needn't restart
VOICE_ONLY = frozenset(voice.KEYS)  # voice mode reads these when it starts a session: nothing restarts
EXPIRED = "That approval expired or was already answered."


class App:
    """Owns the Router and the server. The shell (main.py) sets on_show, on_quit and hotkey."""

    def __init__(self, service, *, store=None, ui_dir=None, keepalive: float = 15):
        self.service = service
        self.store = store if store is not None else (getattr(service, "store", None) or settings.Store())
        self.ui_dir = Path(ui_dir or UI_DIR).resolve()
        self.keepalive = keepalive
        self.secret = secrets.token_urlsafe(32)
        self.instance_token = secrets.token_urlsafe(32)
        self.autostart_telegram = False  # start the bridge with the service when it's set up and wanted
        self.hotkey = None  # an object with keys, ok and error once the shell tried to register it
        self.on_show = None
        self.on_quit = None
        self.server = None
        self.port = 0
        self.hosts: set[str] = set()
        self._codes: dict[str, float] = {}
        self._lock = threading.Lock()
        self._closing = threading.Event()
        self.router = Router()
        self._add_routes()
        self.onboarding = onboarding.register(self.router, service, self.store, shell=self)  # /api/onboarding, /onboarding/
        self.voice = voice.register(self.router, service, self.store)  # /api/voice

    # --- lifecycle --------------------------------------------------------
    def start(self, port: int = 0) -> "App":
        self.server = make_server(self.router, "127.0.0.1", port, authorize=self.authorize, check=self._check_host,
                                  fallback=self.static, extra_headers=SECURITY_HEADERS, on_error=log_error)
        self.port = self.server.server_address[1]
        self.hosts = {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}
        serve_in_thread(self.server)
        return self

    def close(self) -> None:
        self._closing.set()
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()

    @property
    def origin(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    def login_url(self) -> str:
        """A link that signs one window in. It works once, within CODE_TTL seconds."""
        code = secrets.token_urlsafe(24)
        now = time.monotonic()
        with self._lock:
            self._codes = {c: t for c, t in self._codes.items() if t > now}
            self._codes[code] = now + CODE_TTL
        return f"{self.origin}/auth?code={code}"

    def start_service(self):
        """Start the engine (it waits in needs_consent until the notice is accepted) and, when it
        runs, Telegram if that's set up and wanted."""
        status = self.service.start()
        if self.autostart_telegram and status.engine != "needs_consent" and self._telegram_wanted():
            self.service.start_telegram()
        return status

    # --- auth ---------------------------------------------------------------
    def authorize(self, req: Request) -> bool:
        if req.method not in ("GET", "HEAD") and req.header("Origin").lower() != f"http://{req.header('Host').lower()}":
            raise HTTPError(403, "Alfred only takes changes from its own window.")
        return _same(req.header(KEY_HEADER), self.secret)

    def _check_host(self, req: Request) -> None:
        """Every request, static files included. A web page that points its own domain name at
        127.0.0.1 still sends that name as Host."""
        if req.header("Host").lower() not in self.hosts:
            raise HTTPError(403, "Alfred only answers its own window.")

    def _take_code(self, code: str) -> bool:
        now = time.monotonic()
        with self._lock:
            found = None
            for known, expires in self._codes.items():
                if _same(code, known) and expires > now:
                    found = known
            if found is None:
                return False
            del self._codes[found]
            return True

    def auth(self, req: Request) -> Response:
        """The page with this launch's key in a <meta>; app.js moves it to sessionStorage."""
        if not self._take_code(req.query.get("code", "")):
            return Response.text(_page("This sign-in link has already been used or is too old. Open Alfred again "
                                       "from its tray icon or the Start menu."), 403, TYPES[".html"])
        try:
            html = (self.ui_dir / "index.html").read_text(encoding="utf-8")
        except OSError as e:
            raise HTTPError(404, "Not found.") from e
        meta = f'<meta name="{KEY_META}" content="{self.secret}">'
        html, found = re.subn(r"<head\b[^>]*>", lambda m: m.group(0) + meta, html, count=1)
        if not found:
            raise HTTPError(500, "Alfred's page has no <head>.")
        return Response(200, html.encode("utf-8"), {"Content-Type": TYPES[".html"], "Cache-Control": "no-store"})

    def end_voice(self, reason: str = "closed") -> None:
        """Tell the window to end voice mode (over the event feed), e.g. when it was closed to the tray."""
        self.service.bus.publish("voice_off", reason=reason)

    def instance_show(self, req: Request) -> Response:
        if not _same(req.header("X-Alfred-Instance"), self.instance_token):
            raise HTTPError(403, "Not Alfred.")
        if self.on_show is not None:
            self.on_show()
        return Response.json({"ok": True})

    # --- static UI ----------------------------------------------------------
    def static(self, req: Request) -> Response:
        path = unquote(req.path)
        parts = (path.strip("/") or "index.html").split("/")
        if any(not p or p.startswith(".") or any(c in p for c in '\\:\0') for p in parts):
            raise HTTPError(404, "Not found.")
        file = self.ui_dir.joinpath(*parts).resolve()
        kind = TYPES.get(file.suffix.lower())
        if kind is None or not file.is_relative_to(self.ui_dir) or not file.is_file():
            raise HTTPError(404, "Not found.")
        cache = "no-store" if file.suffix.lower() == ".html" else "no-cache"
        return Response(200, file.read_bytes(), {"Content-Type": kind, "Cache-Control": cache})

    # --- API ------------------------------------------------------------------
    def _add_routes(self) -> None:
        add = self.router.add
        add("GET", "/api/health", lambda req: Response.json({"ok": True, "app": "Alfred"}), auth=False)
        add("GET", "/auth", self.auth, auth=False)
        add("POST", "/api/instance/show", self.instance_show, auth=False)
        add("GET", "/api/status", lambda req: Response.json(self.status()))
        add("GET", "/api/events", self.events)
        add("POST", "/api/command", self.command)
        add("POST", "/api/stop", lambda req: Response.json({"stopped": self.service.stop("desktop")}))
        add("POST", "/api/approval", self.approval)
        add("POST", "/api/reset", self.reset)
        add("POST", "/api/restart", self.restart)
        add("GET", "/api/settings", self.get_settings)
        add("PUT", "/api/settings", self.put_settings)
        add("PUT", "/api/secrets/{name}", self.put_secret)
        add("DELETE", "/api/secrets/{name}", self.delete_secret)
        add("POST", "/api/telegram", self.telegram)
        add("GET", "/api/audit", self.audit)
        add("GET", "/api/consent", self.get_consent)
        add("POST", "/api/consent", self.post_consent)
        add("POST", "/api/quit", self.quit)

    def status(self) -> dict:
        out = dataclasses.asdict(self.service.status())
        hotkey = self.hotkey
        out.update(
            consent=consent.accepted(),
            telegram_error=getattr(getattr(self.service, "telegram", None), "last_error", ""),
            hotkey=None if hotkey is None else {"keys": hotkey.keys, "ok": hotkey.ok, "error": hotkey.error},
            version=__version__,
            quit=self.on_quit is not None,
        )
        return out

    def events(self, req: Request) -> Response:
        """Every event after Last-Event-ID (or ?after=; neither: all that are kept), then new ones as
        they happen, with a keep-alive comment when nothing happened for `keepalive` seconds."""
        cursor = _int(req.header("Last-Event-ID") or req.query.get("after"))

        def stream():
            after = cursor
            while not self._closing.is_set():
                batch = self.service.events(after, timeout=self.keepalive)
                if not batch:
                    yield {}
                for event in batch:
                    after = event["seq"]
                    yield event

        return Response.sse(stream(), id_key="seq")

    def command(self, req: Request) -> Response:
        text = req.json().get("text")
        if not isinstance(text, str) or not text.strip():
            raise HTTPError(400, "Type what you want Alfred to do first.")
        try:
            return Response.json({"id": self.service.submit(text, "desktop")})
        except NotReady as e:
            raise HTTPError(409, str(e) or "Alfred isn't ready yet. Try again in a few seconds.") from e
        except QueueFull as e:
            raise HTTPError(429, str(e) or "Alfred has too many commands waiting.") from e
        except ValueError as e:
            raise HTTPError(400, str(e)) from e

    def approval(self, req: Request) -> Response:
        body = req.json()
        token = body.get("token")
        if not isinstance(token, str) or not token:
            raise HTTPError(400, "Which approval? The token is missing.")
        if not self.service.approve(token, body.get("approved") is True, by="desktop"):
            raise HTTPError(409, EXPIRED)
        return Response.json({"ok": True})

    def reset(self, req: Request) -> Response:
        if not self.service.reset_conversation():
            raise HTTPError(409, "Alfred isn't running right now.")
        return Response.json({"ok": True})

    def restart(self, req: Request) -> Response:
        self.service.restart_engine("user")
        return Response.json({"ok": True})

    def quit(self, req: Request) -> Response:
        if self.on_quit is None:
            raise HTTPError(409, "Close Alfred where you started it.")
        threading.Thread(target=self.on_quit, daemon=True, name="alfred-quit").start()
        return Response.json({"ok": True})

    # --- settings -----------------------------------------------------------
    def get_settings(self, req: Request) -> Response:
        secrets_out = {}
        for key in sorted(settings.SECRET_KEYS):
            hint = self.store.secret_hint(key)
            secrets_out[key] = {"set": bool(hint), "hint": hint}
        return Response.json({
            "settings": self.store.values(),
            "editable": list(SETTINGS),
            "defaults": DEFAULTS,
            "secrets": secrets_out,
            "secrets_saved": bool(getattr(self.store.secrets, "persistent", False)),
            "file": str(self.store.path),
            "telegram": self._telegram_wanted(),
        })

    def put_settings(self, req: Request) -> Response:
        body = req.json()
        if not body:
            raise HTTPError(400, "Nothing to save.")
        changes = {}
        for key, value in body.items():
            if settings.is_secret(key):
                raise HTTPError(400, "Save API keys and tokens under API keys.")
            if key not in SETTINGS:
                raise HTTPError(400, f"{key} can't be changed from the app.")
            changes[key] = value = _text(key, value)
            if value and not SETTINGS[key].fullmatch(value):
                raise HTTPError(400, f"{LABELS[key]}: that value doesn't look right.")
        saved = self.store.values()
        changed = {key for key, value in changes.items() if saved.get(key, "") != value}
        for key in changed:
            self.store.set(key, changes[key])  # an empty value goes back to the default
        restarting = self._apply(changed)
        return Response.json({"saved": sorted(changed), "restarting": restarting})

    def put_secret(self, req: Request, name: str) -> Response:
        _secret_name(name)
        value = req.json().get("value")
        if not isinstance(value, str) or not value.strip():
            raise HTTPError(400, "Paste the key first.")
        try:
            self.store.set_secret(name, value)
        except ValueError as e:
            raise HTTPError(400, str(e)) from e
        restarting = self._apply({name})
        return Response.json({"set": True, "hint": self.store.secret_hint(name), "restarting": restarting})

    def delete_secret(self, req: Request, name: str) -> Response:
        _secret_name(name)
        had = self.store.has_secret(name)
        self.store.delete_secret(name)
        restarting = self._apply({name}) if had else False
        return Response.json({"set": False, "hint": "", "restarting": restarting})

    def telegram(self, req: Request) -> Response:
        on = req.json().get("on") is True
        self.store.set("JARVIS_TELEGRAM", "1" if on else "0")
        if on:
            self.service.start_telegram()
        else:
            self.service.stop_telegram()
        tg = getattr(self.service, "telegram", None)
        return Response.json({"state": self.service.status().telegram, "error": getattr(tg, "last_error", "")})

    def _apply(self, keys: set) -> bool:
        """Restart what reads the changed settings. True if the engine restarts."""
        engine = bool(keys - TELEGRAM_ONLY - VOICE_ONLY)
        if engine:
            self.service.restart_engine("settings")
        if keys & TELEGRAM_ONLY:
            threading.Thread(target=self._restart_telegram, daemon=True, name="alfred-telegram-restart").start()
        return engine

    def _restart_telegram(self) -> None:
        if self.service.status().telegram != "off":
            self.service.stop_telegram()
        elif not (self.autostart_telegram and self._telegram_wanted() and consent.accepted()):
            return
        self.service.start_telegram()

    def _telegram_wanted(self) -> bool:
        return self.store.get("JARVIS_TELEGRAM", "1") != "0"

    # --- activity and consent -------------------------------------------------
    def audit(self, req: Request) -> Response:
        limit = min(max(_int(req.query.get("limit")) or 200, 1), 1000)
        path = config.data_dir() / "audit.jsonl"
        return Response.json({"entries": tail_jsonl(path, limit), "file": str(path)})

    def get_consent(self, req: Request) -> Response:
        return Response.json({"accepted": consent.accepted(), "version": consent.TERMS_VERSION,
                              "notice_markdown": consent.notice_markdown()})

    def post_consent(self, req: Request) -> Response:
        version = req.json().get("version")
        if version is not None and version != consent.TERMS_VERSION:
            raise HTTPError(409, "The notice changed while it was open. Read the new one, then agree.")
        consent.record("app")
        self.start_service()
        return Response.json({"accepted": True, "status": self.status()})


def tail_jsonl(path: Path, limit: int, max_bytes: int = 8 * 1024 * 1024) -> list[dict]:
    """The last `limit` JSON objects in a .jsonl file, newest first. Bad lines are skipped."""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            pos = f.tell()
            data = b""
            while pos > 0 and data.count(b"\n") <= limit and len(data) < max_bytes:
                step = min(64 * 1024, pos)
                pos -= step
                f.seek(pos)
                data = f.read(step) + data
    except OSError:
        return []
    lines = data.splitlines()
    if pos > 0:
        lines = lines[1:]  # probably cut in the middle
    out = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            out.append(entry)
            if len(out) >= limit:
                break
    return out


def _same(a: str, b: str) -> bool:
    return hmac.compare_digest(str(a).encode("utf-8"), str(b).encode("utf-8"))


def _int(value) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _text(key: str, value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    if not isinstance(value, str):
        raise HTTPError(400, f"{LABELS[key]}: that value doesn't look right.")
    return value.strip()


ERROR_LOG_MAX = 512 * 1024


def log_error(req: Request, error: BaseException) -> None:
    """Note an unexpected error in data_dir()/errors.log for whoever fixes it: when, which route, the type
    and where in the code. The message is kept only for an ImportError (a module name); any other message
    could hold a key or a token. The log restarts when it passes ERROR_LOG_MAX."""
    path = config.data_dir() / "errors.log"
    detail = f": {error}" if isinstance(error, ImportError) else ""
    where = "".join(traceback.format_tb(error.__traceback__))
    entry = f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {req.method} {req.path}\n{where}{type(error).__name__}{detail}\n\n"
    with _ERROR_LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            mode = "w" if path.exists() and path.stat().st_size > ERROR_LOG_MAX else "a"
            with open(path, mode, encoding="utf-8") as f:
                f.write(entry)
        except OSError:
            pass


_ERROR_LOCK = threading.Lock()


def _secret_name(name: str) -> None:
    if not settings.is_secret(name):
        raise HTTPError(404, "No such key.")


def _page(message: str) -> str:
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Alfred</title>'
            '<link rel="stylesheet" href="/app.css"></head><body class="plain"><main class="plain-page">'
            f"<h1>Alfred</h1><p>{message}</p></main></body></html>")
