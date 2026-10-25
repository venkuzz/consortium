"""
Frontend-serving + contract smoke test (no browser).

Boots the real server in a background thread on an ephemeral port, then:
  - confirms /, /app.css, /app.js are served with correct content types
  - drives the §4 API the way the webui does (create -> plan -> register ->
    claim -> submit -> review) and asserts the JSON shapes the JS relies on
  - connects to /api/events and confirms a `snapshot` frame arrives first with
    the {project,tasks,workers,stats} keys the JS destructures

Run from agent-mesh/:  python -m tests.test_webui_serving
"""
import os
import sys
import json
import time
import threading
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("CONSORTIUM_ALLOW_MOCK_WORK", "1")  # test drives a mock worker round-trip
from http.server import ThreadingHTTPServer
from server.app import Handler

PORT = 8231
BASE = f"http://127.0.0.1:{PORT}"


def _get(path):
    with urllib.request.urlopen(BASE + path, timeout=5) as r:
        return r.status, r.headers.get("Content-Type", ""), r.read()


def _post(path, body=None):
    data = json.dumps(body or {}).encode() if body is not None else b"{}"
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"content-type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, json.loads(r.read().decode())


def run():
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    time.sleep(0.4)
    try:
        print("=== webui serving + contract ===")

        # 1. static assets
        s, ct, body = _get("/")
        assert s == 200 and "text/html" in ct, (s, ct)
        assert b"CONSORTIUM" in body and b"/app.js" in body, "index.html not served"
        s, ct, _ = _get("/app.css")
        assert s == 200 and "text/css" in ct, (s, ct)
        s, ct, js = _get("/app.js")
        assert s == 200 and "javascript" in ct, (s, ct)
        assert b"EventSource" in js and b"/api/events" in js
        print("  index.html / app.css / app.js served with correct content types")

        # 1b. health endpoint (reachability probe teammates use)
        s, ct, hb = _get("/api/health")
        assert s == 200 and "application/json" in ct, (s, ct)
        health = json.loads(hb)
        assert health["ok"] is True and health["service"] == "consortium"
        for k in ("uptime", "project_state", "workers_online", "sse_clients"):
            assert k in health, f"health missing {k}"
        print("  /api/health returns liveness payload")

        # 2. drive the API the way the UI does
        s, _ = _post("/api/project", {"goal": "Build a landing page"})
        assert s == 200
        s, plan = _post("/api/project/plan", {})
        assert s == 200 and len(plan["tasks"]) >= 1
        assert plan["project"]["state"] == "active"
        print(f"  create+plan OK ({len(plan['tasks'])} tasks, used_llm={plan['used_llm']})")

        s, reg = _post("/api/worker/register", {"owner": "Alice", "provider": "mock"})
        assert s == 200 and reg["worker"]["id"]
        wid = reg["worker"]["id"]

        s, nx = _post(f"/api/worker/{wid}/next")
        assert s == 200 and nx["task"], "no task handed out"
        tid = nx["task"]["id"]
        s, sub = _post(f"/api/worker/{wid}/submit", {"task_id": tid, "output": "<x/>", "ok": True})
        assert s == 200 and sub["task_state"] == "submitted"
        s, rev = _post(f"/api/tasks/{tid}/review", {"decision": "accepted", "note": "ok"})
        assert s == 200 and rev["task"]["state"] == "accepted"
        print("  register/next/submit/review round-trip OK")

        # 3. SSE snapshot shape (read just the first frame)
        with urllib.request.urlopen(BASE + "/api/events", timeout=5) as r:
            buf = b""
            while b"\n\n" not in buf:
                buf += r.read(1)
            frame = buf.decode()
        assert frame.startswith("event: snapshot"), frame[:60]
        data_line = [ln for ln in frame.splitlines() if ln.startswith("data:")][0]
        snap = json.loads(data_line[len("data:"):].strip())
        assert set(snap.keys()) == {"project", "tasks", "workers", "stats"}, snap.keys()
        assert snap["project"]["goal"] == "Build a landing page"
        print("  SSE snapshot frame has {project,tasks,workers,stats} as the JS expects")

        print("WEBUI SERVING TEST PASSED")
    finally:
        srv.shutdown()


if __name__ == "__main__":
    run()
