"""Tiny HTTP layer for the Alfred app: a router, request/response types and a local server.

Everything the window shows (main UI, onboarding, voice) is served from 127.0.0.1 by one
server. Features register routes on a shared Router; the shell decides how requests are
authorised (see app/server.py). Handlers are plain functions: handler(req, **path_params) -> Response.
"""

import json
import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Iterable
from urllib.parse import parse_qsl, urlsplit

MAX_BODY = 1024 * 1024  # local UI requests are small (settings, SDP offers)


class HTTPError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


@dataclass
class Request:
    method: str
    path: str
    query: dict = field(default_factory=dict)
    headers: dict = field(default_factory=dict)  # lower-cased names
    body: bytes = b""
    client: str = ""

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)

    def json(self) -> dict:
        try:
            data = json.loads(self.body or b"{}")
        except ValueError as e:
            raise HTTPError(400, "Body must be JSON.") from e
        if not isinstance(data, dict):
            raise HTTPError(400, "Body must be a JSON object.")
        return data


@dataclass
class Response:
    status: int = 200
    body: bytes = b""
    headers: dict = field(default_factory=dict)
    stream: Iterable[bytes] | None = None  # set for server-sent events

    @classmethod
    def json(cls, obj, status: int = 200) -> "Response":
        return cls(status, json.dumps(obj, ensure_ascii=False).encode(), {"Content-Type": "application/json; charset=utf-8"})

    @classmethod
    def text(cls, text: str, status: int = 200, content_type: str = "text/plain; charset=utf-8") -> "Response":
        return cls(status, text.encode(), {"Content-Type": content_type})

    @classmethod
    def sse(cls, events: Iterable[dict], id_key: str = "id") -> "Response":
        """Server-sent events. Each dict may carry "id" (or id_key) and "event"; the rest is sent as
        JSON data. Yield {} to send a keep-alive comment."""

        def encode():
            for ev in events:
                if not ev:
                    yield b": keep-alive\n\n"
                    continue
                ev = dict(ev)
                head = ""
                if id_key in ev:
                    head += f"id: {ev[id_key]}\n"
                if "event" in ev:
                    head += f"event: {ev.pop('event')}\n"
                yield (head + f"data: {json.dumps(ev, ensure_ascii=False)}\n\n").encode()

        return cls(200, b"", {"Content-Type": "text/event-stream", "Cache-Control": "no-store"}, encode())


Handler = Callable[..., Response]


class Router:
    def __init__(self):
        self._routes: list[tuple[str, re.Pattern, Handler, bool]] = []

    def add(self, method: str, path: str, handler: Handler, *, auth: bool = True) -> None:
        """path may contain {name} segments, passed to the handler as keyword arguments."""
        pattern = re.compile("^" + re.sub(r"\{(\w+)\}", r"(?P<\1>[^/]+)", path) + "$")
        self._routes.append((method.upper(), pattern, handler, auth))

    def match(self, method: str, path: str) -> tuple[Handler, dict, bool] | None:
        """(handler, params, auth) for the route, None if nothing matches the path.
        Raises HTTPError(405) if the path exists for another method."""
        seen_path = False
        for m, pattern, handler, auth in self._routes:
            found = pattern.match(path)
            if found:
                seen_path = True
                if m == method.upper():
                    return handler, found.groupdict(), auth
        if seen_path:
            raise HTTPError(405, "Method not allowed.")
        return None


def make_server(
    router: Router,
    host: str = "127.0.0.1",
    port: int = 0,
    *,
    authorize: Callable[[Request], bool] | None = None,
    check: Callable[[Request], None] | None = None,
    fallback: Handler | None = None,
    extra_headers: dict | None = None,
    on_error: Callable[[Request, BaseException], None] | None = None,
) -> ThreadingHTTPServer:
    """A threaded server for router. check(req) runs first for every request, routes and static
    files alike, and refuses one by raising HTTPError. authorize(req) guards routes added with
    auth=True (None allows everything; tests use that). fallback(req) answers unmatched GETs (static files).
    on_error(req, exc) hears about every unexpected exception (the app logs them); it must not raise."""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 75

        def log_message(self, *args):  # keep the console quiet; the app logs what matters
            pass

        def _refuse(self, status: int, message: str):
            resp = Response.json({"error": message}, status)
            resp.headers["Connection"] = "close"  # the unread body would be taken for the next request
            self._send(resp)

        def _handle(self):
            parts = urlsplit(self.path)
            if self.headers.get("Transfer-Encoding"):
                return self._refuse(411, "Send a Content-Length.")
            sizes = self.headers.get_all("Content-Length") or ["0"]
            if len(sizes) > 1 or not re.fullmatch(r"[0-9]{1,15}", sizes[0].strip()):
                return self._refuse(400, "Bad Content-Length.")
            length = int(sizes[0])
            if length > MAX_BODY:
                return self._refuse(413, "Request too large.")
            req = Request(
                method=self.command,
                path=parts.path,
                query=dict(parse_qsl(parts.query)),
                headers={k.lower(): v for k, v in self.headers.items()},
                body=self.rfile.read(length) if length else b"",
                client=self.client_address[0],
            )
            try:
                if check is not None:
                    check(req)
                found = router.match(req.method, req.path)
                if found is None:
                    if fallback is not None and req.method in ("GET", "HEAD"):
                        return self._send(fallback(req))
                    raise HTTPError(404, "Not found.")
                handler, params, needs_auth = found
                if needs_auth and authorize is not None and not authorize(req):
                    raise HTTPError(401, "Not signed in to Alfred.")
                resp = handler(req, **params)
            except HTTPError as e:
                resp = Response.json({"error": e.message}, e.status)
            except Exception as e:  # noqa: BLE001 - never leak a traceback to the page
                if on_error is not None:
                    try:
                        on_error(req, e)
                    except Exception:  # noqa: BLE001
                        pass
                resp = Response.json({"error": unexpected(e)}, 500)
            self._send(resp)

        def _send(self, resp: Response):
            self.send_response(resp.status)
            for k, v in {**(extra_headers or {}), **resp.headers}.items():
                self.send_header(k, v)
            if resp.stream is None:
                self.send_header("Content-Length", str(len(resp.body)))
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(resp.body)
                return
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for chunk in resp.stream:
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            self.close_connection = True

        do_GET = do_POST = do_PUT = do_DELETE = do_HEAD = _handle

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


def unexpected(error: BaseException) -> str:
    """What the page shows for an error nobody planned for: its type, and the missing part's name for an
    ImportError (a module name holds no secrets). Other messages could hold a key, so they stay out."""
    if isinstance(error, ImportError):
        return (f"Alfred is missing one of its parts ({error}). Reinstall Alfred; if that doesn't help, "
                "send this message to the Alfred team.")
    return f"Something went wrong in Alfred ({type(error).__name__}). Try again; the details are in Alfred's error log."


def serve_in_thread(server: ThreadingHTTPServer) -> threading.Thread:
    t = threading.Thread(target=server.serve_forever, name="alfred-http", daemon=True)
    t.start()
    return t
