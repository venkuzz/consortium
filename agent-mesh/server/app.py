"""
Consortium server — Agent A (WORK_SPLIT.md §4, §5 Phase 1).

Zero-dependency stdlib HTTP server. The leader runs this; teammates open the
LAN URL. Implements every endpoint in §4:

  GET  /api/project                     GET  /api/tasks
  POST /api/project                     POST /api/tasks
  POST /api/project/plan                POST /api/tasks/{id}/review
  POST /api/project/state
  POST /api/worker/register             GET  /api/workers
  POST /api/worker/{id}/heartbeat       POST /api/worker/{id}/unregister
  POST /api/worker/{id}/next            GET  /api/events        (SSE)
  POST /api/worker/{id}/submit

Also serves a built frontend from server/static/ if present (the frontend agent
drops its build there), and a minimal fallback page otherwise.

Run:  python -m server.app           (from agent-mesh/, listens on 0.0.0.0:8000)
      PORT=9000 python -m server.app
"""
from __future__ import annotations

import os
import re
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from core.store import Store
from .sse import SSEHub
from . import routes_project as rp
from . import routes_worker as rw


def _load_dotenv(path=".env"):
    """Minimal .env loader (no python-dotenv dependency)."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# ── wiring: one hub, one store, store broadcasts through the hub ────────────
HUB = SSEHub()
STORE = Store(broadcast=HUB.broadcast)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# ── route table: (method, compiled_path_regex) -> handler(store, body, params)
# Path params use named groups, e.g. (?P<id>[^/]+).
_ROUTES = [
    ("GET",  r"^/api/project$",                 rp.get_project),
    ("POST", r"^/api/project$",                 rp.create_project),
    ("POST", r"^/api/project/plan$",            rp.plan_project),
    ("POST", r"^/api/project/state$",           rp.set_state),
    ("GET",  r"^/api/tasks$",                    rp.list_tasks),
    ("POST", r"^/api/tasks$",                    rp.add_task),
    ("POST", r"^/api/tasks/(?P<id>[^/]+)/review$", rp.review_task),
    ("POST", r"^/api/worker/register$",         rw.register),
    ("POST", r"^/api/worker/(?P<id>[^/]+)/heartbeat$", rw.heartbeat),
    ("POST", r"^/api/worker/(?P<id>[^/]+)/next$",      rw.next_task),
    ("POST", r"^/api/worker/(?P<id>[^/]+)/submit$",    rw.submit),
    ("POST", r"^/api/worker/(?P<id>[^/]+)/unregister$", rw.unregister),
    ("GET",  r"^/api/workers$",                 rw.list_workers),
]
_COMPILED = [(m, re.compile(p), h) for (m, p, h) in _ROUTES]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet console
        pass

    # ── helpers ─────────────────────────────────────────────────────────────
    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    def _query(self):
        if "?" not in self.path:
            return {}
        q = self.path.split("?", 1)[1]
        out = {}
        for pair in q.split("&"):
            if "=" in pair:
                k, v = pair.split("=", 1)
                out[k] = v
        return out

    def _path_only(self):
        return self.path.split("?", 1)[0]

    def _match(self, method):
        path = self._path_only()
        for m, rx, handler in _COMPILED:
            if m != method:
                continue
            mo = rx.match(path)
            if mo:
                return handler, mo.groupdict()
        return None, None

    # ── CORS preflight ──────────────────────────────────────────────────────
    def do_OPTIONS(self):
        self._send(204, b"")

    # ── GET ─────────────────────────────────────────────────────────────────
    def do_GET(self):
        path = self._path_only()
        if path == "/api/events":
            return self._stream_events()

        handler, params = self._match("GET")
        if handler:
            if params is not None:
                params.update(self._query())
            code, body = handler(STORE, {}, params)
            return self._send(code, body)

        # Static / frontend.
        if path == "/" or not path.startswith("/api"):
            return self._serve_static(path)
        self._send(404, {"error": "not found"})

    # ── POST ────────────────────────────────────────────────────────────────
    def do_POST(self):
        handler, params = self._match("POST")
        if not handler:
            return self._send(404, {"error": "not found"})
        body = self._read_json()
        try:
            code, resp = handler(STORE, body, params or {})
        except Exception as e:  # never leak a stack trace to the client
            return self._send(500, {"error": f"server error: {e}"})
        self._send(code, resp)

    # ── SSE stream ──────────────────────────────────────────────────────────
    def _stream_events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        sub = HUB.subscribe()
        try:
            # 1. Immediately send a full snapshot (§4.4 contract).
            snap = f"event: snapshot\ndata: {json.dumps(STORE.snapshot())}\n\n"
            self.wfile.write(snap.encode("utf-8"))
            self.wfile.flush()
            # 2. Stream events; heartbeat comment every ~15s to keep the pipe open.
            last_ping = time.time()
            while True:
                chunk = sub.get(timeout=1.0)
                if chunk is not None:
                    self.wfile.write(chunk.encode("utf-8"))
                    self.wfile.flush()
                if time.time() - last_ping > 15:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_ping = time.time()
                if not sub.alive:
                    break
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        finally:
            HUB.unsubscribe(sub)

    # ── static file serving ──────────────────────────────────────────────────
    def _serve_static(self, path):
        if path == "/":
            path = "/index.html"
        safe = os.path.normpath(path).lstrip("/\\")
        full = os.path.join(STATIC_DIR, safe)
        # Prevent path traversal outside STATIC_DIR.
        if not os.path.abspath(full).startswith(os.path.abspath(STATIC_DIR)):
            return self._send(403, {"error": "forbidden"})
        if os.path.isfile(full):
            ctype = _content_type(full)
            with open(full, "rb") as f:
                return self._send(200, f.read(), ctype)
        # No frontend build yet -> helpful fallback page.
        return self._send(200, _FALLBACK_HTML, "text/html; charset=utf-8")


def _content_type(path):
    ext = os.path.splitext(path)[1].lower()
    return {
        ".html": "text/html; charset=utf-8",
        ".js": "text/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".json": "application/json",
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".ico": "image/x-icon",
    }.get(ext, "application/octet-stream")


_FALLBACK_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Consortium</title>
<style>body{font-family:system-ui,sans-serif;background:#0e1116;color:#e6e9ef;
max-width:680px;margin:40px auto;padding:0 20px;line-height:1.5}
code{background:#171c24;padding:2px 6px;border-radius:5px}
a{color:#5b9dff}</style></head>
<body>
<h1>Consortium backend is running</h1>
<p>The API is live. The frontend build isn't present yet
(<code>server/static/</code> is empty).</p>
<p>API contract: see <code>agent-mesh/WORK_SPLIT.md</code> §4. Live state stream:
<code>GET /api/events</code>.</p>
<h3>Quick check</h3>
<pre>curl http://localhost:8000/api/project
curl -X POST http://localhost:8000/api/project -d '{"goal":"demo"}'
curl -X POST http://localhost:8000/api/project/plan -d '{}'</pre>
</body></html>"""


def main():
    port = int(os.getenv("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Consortium server running at http://0.0.0.0:{port}")
    print(f"Teammates on the LAN join at  http://<your-LAN-ip>:{port}")
    print("API contract: WORK_SPLIT.md §4   |   live stream: GET /api/events")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
