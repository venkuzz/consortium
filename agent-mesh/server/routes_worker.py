"""
Worker route handlers — Agent A (WORK_SPLIT.md §4.3).

Handlers take (store, body, params) -> (status, dict). Provider validation and
optional room_code gating live here.
"""
from __future__ import annotations

import os
import time

from core.store import PROVIDER_LABELS

# Optional shared room code. If the host sets CONSORTIUM_ROOM_CODE, registers
# must match it; otherwise registration is open (§4.1).
ROOM_CODE = os.getenv("CONSORTIUM_ROOM_CODE", "").strip()


# POST /api/worker/register  {owner, provider, api_key?, model?, room_code?}
def register(store, body, params):
    if ROOM_CODE and (body.get("room_code") or "").strip() != ROOM_CODE:
        return 403, {"error": "invalid room code"}
    owner = (body.get("owner") or "").strip()
    provider = (body.get("provider") or "").strip()
    if not owner:
        return 400, {"error": "owner required"}
    if provider not in PROVIDER_LABELS:
        return 400, {"error": f"unknown provider (use one of {list(PROVIDER_LABELS)})"}
    w = store.register_worker(owner, provider, body.get("api_key"), body.get("model"),
                              worker_id=(body.get("worker_id") or "").strip() or None)
    proj = store.get_project()
    return 200, {
        "worker": {"id": w.id, "display": w.display,
                   "provider": w.provider, "status": w.status},
        "project_state": proj.state if proj else None,
    }


# POST /api/worker/{id}/heartbeat
def heartbeat(store, body, params):
    w = store.heartbeat(params.get("id"))
    if not w:
        return 404, {"error": "unknown worker"}
    proj = store.get_project()
    return 200, {"ok": True, "project_state": proj.state if proj else None}


# POST /api/worker/{id}/next
def next_task(store, body, params):
    try:
        wait = max(0.0, min(float(params.get("wait", 0) or 0), 30.0))
    except (TypeError, ValueError):
        wait = 0.0
    deadline = time.monotonic() + wait
    while True:
        result = store.claim_next(params.get("id"))
        if result.get("error"):
            return 404, result
        if result.get("task") or result.get("stop") or not result.get("wait"):
            return 200, result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 200, result
        # ThreadingHTTPServer gives each worker its own request thread. A short
        # sleep keeps this stdlib implementation responsive without hot-looping.
        time.sleep(min(0.25, remaining))


# POST /api/worker/{id}/submit  {task_id, output, ok?, error?, elapsed?}
def submit(store, body, params):
    worker_id = params.get("id")
    task_id = (body.get("task_id") or "").strip()
    if not task_id:
        return 400, {"error": "task_id required"}
    try:
        store.submit(
            worker_id, task_id,
            output=body.get("output", ""),
            ok=bool(body.get("ok", True)),
            error=body.get("error", ""),
            elapsed=float(body.get("elapsed", 0.0) or 0.0),
        )
    except ValueError as e:
        return 400, {"error": str(e)}
    return 200, {"ok": True, "task_state": "submitted"}


# GET /api/workers
def list_workers(store, body, params):
    return 200, {"workers": [w.to_dict() for w in store.list_workers()]}


# POST /api/worker/{id}/unregister
def unregister(store, body, params):
    store.remove_worker(params.get("id"))
    return 200, {"ok": True}
