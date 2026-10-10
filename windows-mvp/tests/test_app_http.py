"""The shared HTTP layer the app's features register routes on."""

import http.client
import json
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
