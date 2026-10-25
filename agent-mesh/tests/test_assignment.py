"""
Tests for leader-directed task assignment (pin a task to a specific worker).

A pinned task:
  - goes ONLY to its assignee on claim (other workers skip it),
  - jumps ahead of unpinned tasks for that assignee,
  - releases its pin if the assignee leaves or goes offline.

Run from agent-mesh/:  python -m tests.test_assignment
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.store import Store, WORKER_TIMEOUT
from core.state import ACTIVE, ACCEPTED
from server import routes_project as rp
from server import routes_worker as rw


def _active_store():
    s = Store()
    s.create_project("assign demo")
    s.set_state(ACTIVE)
    return s


def test_pinned_task_goes_to_assignee_only():
    s = _active_store()
    bob = s.register_worker("Bob", "mock").id
    carol = s.register_worker("Carol", "mock").id
    # One task for each; pin t1 to Carol explicitly.
    t1 = s.add_task("Carol's task", "do C")
    t2 = s.add_task("open task", "do O")
    s.assign_task(t1.id, carol)

    # Bob pulls first — must NOT get Carol's pinned task; gets the open one.
    r = s.claim_next(bob)
    assert r["task"]["id"] == t2.id, f"Bob wrongly got {r['task']['id']}"
    # Carol pulls — gets her pinned task.
    r2 = s.claim_next(carol)
    assert r2["task"]["id"] == t1.id, f"Carol should get her pinned task, got {r2}"
    print("  pinned task goes only to its assignee OK")


def test_pin_jumps_the_queue_for_assignee():
    s = _active_store()
    bob = s.register_worker("Bob", "mock").id
    _t1 = s.add_task("first open", "a")       # lower seq, unpinned
    t2 = s.add_task("pinned to bob", "b")
    s.assign_task(t2.id, bob)
    # Even though t1 has a lower seq, Bob's pinned t2 wins.
    r = s.claim_next(bob)
    assert r["task"]["id"] == t2.id, "pin did not jump the queue"
    print("  pinned task jumps the queue for its assignee OK")


def test_assign_via_route_and_add_with_assignee():
    s = _active_store()
    code, reg = rw.register(s, {"owner": "Dana", "provider": "mock"}, {})
    dana = reg["worker"]["id"]
    # Add a task pre-pinned to Dana via the /api/tasks route.
    code, body = rp.add_task(s, {"title": "D", "instruction": "x", "assignee": dana}, {})
    assert code == 200 and body["task"]["assignee"] == dana
    tid = body["task"]["id"]
    # Re-assign via the dedicated route to nobody, then back to Dana.
    code, body = rp.assign_task(s, {"worker_id": ""}, {"id": tid})
    assert code == 200 and body["task"]["assignee"] is None
    code, body = rp.assign_task(s, {"worker_id": dana}, {"id": tid})
    assert code == 200 and body["task"]["assignee"] == dana
    # Unknown worker rejected.
    code, body = rp.assign_task(s, {"worker_id": "ghost"}, {"id": tid})
    assert code == 400
    print("  add-with-assignee + assign route OK")


def test_pin_releases_when_worker_leaves():
    s = _active_store()
    bob = s.register_worker("Bob", "mock").id
    carol = s.register_worker("Carol", "mock").id
    t = s.add_task("pinned", "x")
    s.assign_task(t.id, bob)
    s.remove_worker(bob)  # Bob leaves
    # The pin should have released; Carol can now claim it.
    r = s.claim_next(carol)
    assert r["task"] and r["task"]["id"] == t.id, "pin didn't release when Bob left"
    print("  pin releases when assignee leaves OK")


def test_pin_releases_when_worker_goes_stale():
    s = _active_store()
    bob = s.register_worker("Bob", "mock").id
    carol = s.register_worker("Carol", "mock").id
    t = s.add_task("pinned", "x")
    s.assign_task(t.id, bob)
    # Bob goes silent past the timeout.
    s._workers[bob].last_seen = time.time() - (WORKER_TIMEOUT + 5)
    r = s.claim_next(carol)  # sweep runs, releases the pin, Carol claims
    assert r["task"] and r["task"]["id"] == t.id, "pin didn't release on stale worker"
    print("  pin releases when assignee goes offline OK")


def test_other_workers_wait_when_only_pinned_tasks_remain():
    s = _active_store()
    bob = s.register_worker("Bob", "mock").id
    carol = s.register_worker("Carol", "mock").id
    t = s.add_task("only for bob", "x")
    s.assign_task(t.id, bob)
    # Carol has nothing to do (the one task is pinned to Bob).
    r = s.claim_next(carol)
    assert r.get("task") is None and r.get("wait"), "Carol should wait, not steal Bob's task"
    # Bob gets it.
    r2 = s.claim_next(bob)
    assert r2["task"]["id"] == t.id
    print("  non-assignee waits while only others' pinned tasks remain OK")


if __name__ == "__main__":
    print("=== task assignment tests ===")
    test_pinned_task_goes_to_assignee_only()
    test_pin_jumps_the_queue_for_assignee()
    test_assign_via_route_and_add_with_assignee()
    test_pin_releases_when_worker_leaves()
    test_pin_releases_when_worker_goes_stale()
    test_other_workers_wait_when_only_pinned_tasks_remain()
    print("ALL ASSIGNMENT TESTS PASSED")
