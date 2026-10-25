"""
Agent A route-level tests (WORK_SPLIT.md §4) — in-process, no sockets.

Exercises every handler against a fresh Store wired to a capturing SSE hub, so
we verify both the HTTP-shaped responses AND that the right SSE events fire.

Run from agent-mesh/:  python -m tests.test_server_routes
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("CONSORTIUM_ALLOW_MOCK_WORK", "1")  # these tests drive mock workers
from core.store import Store
from core.state import ACTIVE, PAUSED, DONE, ACCEPTED, REJECTED
from server import routes_project as rp
from server import routes_worker as rw


class CapturingHub:
    def __init__(self):
        self.events = []

    def broadcast(self, event, data):
        self.events.append((event, data))


def fresh():
    hub = CapturingHub()
    store = Store(broadcast=hub.broadcast)
    return store, hub


def test_project_lifecycle_routes():
    store, hub = fresh()

    code, body = rp.get_project(store, {}, {})
    assert code == 200 and body["project"] is None

    code, body = rp.create_project(store, {"goal": "Build X"}, {})
    assert code == 200 and body["project"]["state"] == "planning"

    code, body = rp.create_project(store, {"goal": ""}, {})
    assert code == 400, "empty goal must 400"

    code, body = rp.plan_project(store, {}, {})
    assert code == 200
    assert len(body["tasks"]) >= 1
    assert body["project"]["state"] == "active", "plan should activate project"
    assert "tasks_planned" in [e for e, _ in hub.events]

    code, body = rp.get_project(store, {}, {})
    assert body["stats"]["queued"] == len(store.list_tasks())
    print("  project create/plan/get routes OK")


def test_manual_task_and_review():
    store, hub = fresh()
    rp.create_project(store, {"goal": "g"}, {})
    rp.plan_project(store, {}, {})

    code, body = rp.add_task(store, {"title": "Footer", "instruction": "build footer"}, {})
    assert code == 200
    tid = body["task"]["id"]

    code, body = rp.add_task(store, {"title": "", "instruction": ""}, {})
    assert code == 400, "missing title/instruction must 400"

    # Register a worker, claim the footer, submit, then review.
    code, reg = rw.register(store, {"owner": "Alice", "provider": "mock"}, {})
    assert code == 200
    wid = reg["worker"]["id"]

    # Claim until we get the footer task (plan tasks come first by seq).
    got = None
    for _ in range(10):
        code, nx = rw.next_task(store, {}, {"id": wid})
        assert code == 200
        if nx.get("task") is None:
            break
        got = nx["task"]["id"]
        rw.submit(store, {"task_id": got, "output": "<footer>x</footer>"}, {"id": wid})
        rp.review_task(store, {"decision": ACCEPTED, "note": "ok"}, {"id": got})

    # The footer task should have been accepted among them.
    footer = store.get_task(tid)
    assert footer.state == ACCEPTED, f"footer ended {footer.state}"
    print("  manual task + claim/submit/review routes OK")


def test_review_reject_followup():
    store, hub = fresh()
    rp.create_project(store, {"goal": "g"}, {})
    t = store.add_task("thing", "do it")
    store.set_state(ACTIVE)
    code, reg = rw.register(store, {"owner": "Bob", "provider": "mock"}, {})
    wid = reg["worker"]["id"]
    rw.next_task(store, {}, {"id": wid})
    rw.submit(store, {"task_id": t.id, "output": "bad"}, {"id": wid})
    code, body = rp.review_task(store, {
        "decision": REJECTED, "note": "missing form",
        "followup_instruction": "redo with form"}, {"id": t.id})
    assert code == 200
    assert body["followup"] is not None
    assert body["followup"]["instruction"] == "redo with form"
    print("  reject + follow-up route OK")


def test_worker_routes_validation():
    store, hub = fresh()
    rp.create_project(store, {"goal": "g"}, {})

    code, body = rw.register(store, {"owner": "", "provider": "mock"}, {})
    assert code == 400, "missing owner must 400"
    code, body = rw.register(store, {"owner": "A", "provider": "nope"}, {})
    assert code == 400, "bad provider must 400"

    code, reg = rw.register(store, {"owner": "Carol", "provider": "mock"}, {})
    wid = reg["worker"]["id"]

    code, body = rw.heartbeat(store, {}, {"id": wid})
    assert code == 200 and body["ok"]
    code, body = rw.heartbeat(store, {}, {"id": "ghost"})
    assert code == 404, "unknown worker heartbeat must 404"

    code, body = rw.list_workers(store, {}, {})
    assert code == 200 and len(body["workers"]) == 1

    code, body = rw.unregister(store, {}, {"id": wid})
    assert code == 200
    code, body = rw.list_workers(store, {}, {})
    assert len(body["workers"]) == 0
    print("  worker route validation OK")


def test_state_route_rules():
    store, hub = fresh()
    rp.create_project(store, {"goal": "g"}, {})
    rp.plan_project(store, {}, {})  # -> active
    code, body = rp.set_state(store, {"state": "paused"}, {})
    assert code == 200 and body["project"]["state"] == "paused"
    code, body = rp.set_state(store, {"state": "done"}, {})
    assert code == 200 and body["project"]["state"] == "done"
    # done is terminal.
    code, body = rp.set_state(store, {"state": "active"}, {})
    assert code == 409, "done->active must be rejected"
    assert "error" in body
    print("  project state-route rules OK")


def test_snapshot_shape():
    store, hub = fresh()
    rp.create_project(store, {"goal": "g"}, {})
    rp.plan_project(store, {}, {})
    snap = store.snapshot()
    assert set(snap.keys()) == {"project", "tasks", "workers", "stats"}
    assert snap["project"]["goal"] == "g"
    print("  SSE snapshot shape OK")


if __name__ == "__main__":
    print("=== server route tests ===")
    test_project_lifecycle_routes()
    test_manual_task_and_review()
    test_review_reject_followup()
    test_worker_routes_validation()
    test_state_route_rules()
    test_snapshot_shape()
    print("ALL SERVER ROUTE TESTS PASSED")
