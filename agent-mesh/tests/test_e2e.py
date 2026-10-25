"""
End-to-end integration test (WORK_SPLIT.md §5 Phase 3) — SHARED file.

Exercises the WHOLE system wired together through the real pieces:
  - core.store (Agent A)
  - server routes (Agent A)
  - server.lead_facade -> lead.orchestrator (Agent B, real, not stubbed)

Drives a realistic leader + multi-worker run with NO network:
  create project -> plan (real orchestrator) -> register 2 workers ->
  run their claim/submit/review loops until every task is accepted ->
  mark the project done. Asserts the index-based dependency (the integrate
  task) only unlocks after its deps are accepted, and that SSE events fire.

Run from agent-mesh/:  python -m tests.test_e2e
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.store import Store
from core.state import ACTIVE, DONE, ACCEPTED, QUEUED
from server import routes_project as rp
from server import routes_worker as rw
from server import lead_facade


class CapturingHub:
    def __init__(self):
        self.events = []

    def broadcast(self, event, data):
        self.events.append((event, data))


def run():
    print("=== Consortium end-to-end (A+B integrated) ===")
    assert lead_facade.USING_REAL_LEAD, \
        "facade did not load Agent B's real orchestrator — integration not wired!"
    print("  lead_facade is using the REAL Agent B orchestrator")

    hub = CapturingHub()
    store = Store(broadcast=hub.broadcast)

    # 1. Leader creates the project and plans it with the real orchestrator.
    code, _ = rp.create_project(store, {"goal": "Build a landing page"}, {})
    assert code == 200
    code, plan_body = rp.plan_project(store, {}, {})
    assert code == 200
    tasks = store.list_tasks()
    n = len(tasks)
    assert n >= 2, "orchestrator produced too few tasks"
    print(f"  planned {n} tasks via orchestrator (used_llm={plan_body['used_llm']})")
    assert store.get_project().state == ACTIVE

    # Identify any task with real dependencies (the integrate task).
    dep_tasks = [t for t in tasks if t.depends_on]
    assert dep_tasks, "expected at least one task with dependencies"
    integrate = dep_tasks[-1]
    print(f"  '{integrate.title}' depends on {integrate.depends_on}")
    # Its deps must be real ids that exist (index->id mapping worked).
    existing = {t.id for t in tasks}
    assert all(d in existing for d in integrate.depends_on), \
        "dependency ids don't resolve — index->id mapping broken"

    # 2. Two workers register and run their loops.
    code, regA = rw.register(store, {"owner": "Alice", "provider": "mock"}, {})
    code, regB = rw.register(store, {"owner": "Bob", "provider": "mock"}, {})
    wA, wB = regA["worker"]["id"], regB["worker"]["id"]
    print(f"  registered 2 workers: {regA['worker']['display']}, {regB['worker']['display']}")

    # Track that the integrate task is never handed out before its deps accept.
    integrate_claimed_early = False
    loops = 0
    workers = [wA, wB]
    wi = 0
    while True:
        loops += 1
        if loops > 100:
            raise AssertionError("loop did not converge")
        progressed = False
        for w in workers:
            code, nx = rw.next_task(store, {}, {"id": w})
            task = nx.get("task")
            if not task:
                continue
            progressed = True
            # Guard: integrate must not be claimable until deps accepted.
            if task["id"] == integrate.id:
                deps_ok = all(store.get_task(d).state == ACCEPTED
                              for d in integrate.depends_on)
                if not deps_ok:
                    integrate_claimed_early = True
            # Worker "executes" (mock) and submits, leader accepts.
            rw.submit(store, {"task_id": task["id"], "output": f"<out>{task['id']}</out>"},
                      {"id": w})
            rp.review_task(store, {"decision": ACCEPTED, "note": "lgtm"},
                           {"id": task["id"]})
        if not progressed:
            # No task handed out this pass: are we done, or just waiting?
            remaining = [t for t in store.list_tasks()
                         if t.state in (QUEUED, "assigned", "submitted")]
            if not remaining:
                break

    assert not integrate_claimed_early, \
        "integrate task was dispatched before its dependencies were accepted!"

    # 3. Every task accepted.
    states = {t.id: t.state for t in store.list_tasks()}
    assert all(s == ACCEPTED for s in states.values()), f"not all accepted: {states}"
    print(f"  all {n} tasks ACCEPTED; dependency ordering respected")

    # 4. Leader marks the project done; workers get the stop signal.
    code, _ = rp.set_state(store, {"state": DONE}, {})
    assert code == 200
    code, nx = rw.next_task(store, {}, {"id": wA})
    assert nx.get("stop"), "worker should be told to stop when project is done"
    print("  project marked DONE; workers receive stop signal")

    # 5. SSE event sanity.
    names = [e for e, _ in hub.events]
    for required in ("project_state", "tasks_planned", "task_claimed",
                     "task_submitted", "task_reviewed", "worker_joined", "done"):
        assert required in names, f"missing SSE event: {required}"
    print(f"  SSE events fired: {sorted(set(names))}")

    print("END-TO-END INTEGRATION PASSED")


if __name__ == "__main__":
    run()
