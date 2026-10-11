"""The shared HTTP layer the app's features register routes on."""

import http.client
import json
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jarvis.app.http import HTTPError, Response, Router, make_server, serve_in_thread  # noqa: E402


def _server(router, **kw):
    server = make_server(router, **kw)
    serve_in_thread(server)
    return server, server.server_address[1]


def _call(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
    resp = conn.getresponse()
    data = resp.read()
    conn.close()
    return resp.status, resp.getheader("Content-Type", ""), data


def test_routes_params_auth_and_errors():
    router = Router()
    router.add("GET", "/api/health", lambda req: Response.json({"ok": True}), auth=False)
    router.add("PUT", "/api/secrets/{name}", lambda req, name: Response.json({"name": name, "v": req.json()["value"]}))
    router.add("POST", "/api/boom", lambda req: (_ for _ in ()).throw(HTTPError(409, "busy")))
    server, port = _server(router, authorize=lambda req: req.header("X-Alfred") == "1",
                           fallback=lambda req: Response.text("static " + req.path))
    try:
        assert _call(port, "GET", "/api/health")[0] == 200
        assert _call(port, "PUT", "/api/secrets/OPENAI_API_KEY", {"value": "x"})[0] == 401
        status, _, data = _call(port, "PUT", "/api/secrets/OPENAI_API_KEY", {"value": "x"}, {"X-Alfred": "1"})
        assert status == 200 and json.loads(data) == {"name": "OPENAI_API_KEY", "v": "x"}
        assert _call(port, "GET", "/api/secrets/x", headers={"X-Alfred": "1"})[0] == 405
        status, _, data = _call(port, "POST", "/api/boom", {}, {"X-Alfred": "1"})
        assert status == 409 and json.loads(data)["error"] == "busy"
        assert _call(port, "GET", "/index.html")[2] == b"static /index.html"
        assert _call(port, "POST", "/nope", {})[0] == 404
    finally:
        server.shutdown()


def test_server_sent_events():
    router = Router()
    router.add("GET", "/api/events", lambda req: Response.sse([{"id": 1, "event": "reply", "text": "hi"}, {}]), auth=False)
    server, port = _server(router)
    try:
        status, ctype, data = _call(port, "GET", "/api/events")
        assert status == 200 and ctype == "text/event-stream"
        assert data.decode() == 'id: 1\nevent: reply\ndata: {"id": 1, "text": "hi"}\n\n: keep-alive\n\n'
    finally:
        server.shutdown()


def _send_raw(port, data: bytes) -> bytes:
    """Send bytes as they are and read until the server closes. Times out if it waits for a body."""
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        sock.sendall(data)
        out = b""
        while chunk := sock.recv(65536):
            out += chunk
    return out


def test_bad_lengths_are_refused_without_reading_the_body():
    seen = []
    router = Router()
    router.add("PUT", "/api/x", lambda req: seen.append(req.body) or Response.json({"ok": True}), auth=False)
    server, port = _server(router)
    try:
        for header, status in ((b"Content-Length: -1", 400), (b"Content-Length: abc", 400), (b"Content-Length: \xb2", 400),
                               (b"Content-Length: 1\r\nContent-Length: 1", 400), (b"Content-Length: 2000000", 413),
                               (b"Transfer-Encoding: chunked", 411)):
            reply = _send_raw(port, b"PUT /api/x HTTP/1.1\r\nHost: x\r\n" + header + b"\r\n\r\n")
            assert reply.startswith(b"HTTP/1.1 %d " % status) and b"Connection: close" in reply, header
        assert seen == []
        assert _call(port, "PUT", "/api/x", {"a": 1})[0] == 200 and seen == [b'{"a": 1}']
    finally:
        server.shutdown()


def test_check_runs_before_every_route_and_static_file():
    router = Router()
    router.add("GET", "/api/health", lambda req: Response.json({"ok": True}), auth=False)
    router.add("GET", "/api/private", lambda req: Response.json({"ok": True}))

    def check(req):
        if req.header("Host") != "good":
            raise HTTPError(403, "Wrong host.")

    server, port = _server(router, check=check, authorize=lambda req: True, fallback=lambda req: Response.text("static"))
    try:
        for path in ("/api/health", "/api/private", "/index.html"):
            assert _call(port, "GET", path, headers={"Host": "evil"})[0] == 403, path
            assert _call(port, "GET", path, headers={"Host": "good"})[0] == 200, path
        assert _call(port, "POST", "/nope", {}, {"Host": "evil"})[0] == 403
    finally:
        server.shutdown()


def test_unexpected_errors_are_logged_and_name_a_missing_part():
    seen = []
    router = Router()
    router.add("GET", "/api/missing", lambda req: __import__("no_such_alfred_part"), auth=False)
    router.add("GET", "/api/broken", lambda req: {}["sk-ant-secret-in-a-message"], auth=False)
    server, port = _server(router, on_error=lambda req, e: seen.append((req.path, type(e).__name__)))
    try:
        status, _, data = _call(port, "GET", "/api/missing")
        assert status == 500 and "no_such_alfred_part" in json.loads(data)["error"]
        status, _, data = _call(port, "GET", "/api/broken")
        message = json.loads(data)["error"]
        assert status == 500 and "KeyError" in message and "sk-ant" not in message  # messages can hold keys
        assert seen == [("/api/missing", "ModuleNotFoundError"), ("/api/broken", "KeyError")]
    finally:
        server.shutdown()
