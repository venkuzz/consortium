"""
Agent A unit tests for core/store.py (WORK_SPLIT.md §5 Phase 1).

Run from agent-mesh/:  python -m tests.test_store
Covers: claim atomicity under concurrency, dependency gating, lifecycle
transitions, submit/review flow, and stale-claim requeue.
"""
import os
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor

# Allow running as a script from the agent-mesh dir.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.store import Store, WORKER_TIMEOUT
from core.state import (
    ACTIVE, PAUSED, DONE, QUEUED, ASSIGNED, SUBMITTED, ACCEPTED, REJECTED,
    WORKING, IDLE, OFFLINE,
)
from core.lifecycle import validate_transition, TransitionError


def test_lifecycle_rules():
    validate_transition("planning", "active")
    validate_transition("active", "paused")
    validate_transition("paused", "active")
    validate_transition("active", "done")
    for bad_from, bad_to in [("done", "active"), ("planning", "paused")]:
        try:
            validate_transition(bad_from, bad_to)
            assert False, f"{bad_from}->{bad_to} should be illegal"
        except TransitionError:
            pass
    print("  lifecycle rules OK")


def test_claim_atomicity():
    """200 queued tasks, 8 workers racing: every task claimed exactly once."""
    s = Store()
    s.create_project("race test")
    s.set_state(ACTIVE)
    N = 200
    for i in range(N):
        s.add_task(f"task {i}", "do it")
    workers = [s.register_worker(f"w{i}", "mock").id for i in range(8)]

    claimed: list[str] = []
    claim_lock = threading.Lock()

    def drain(wid):
        local = []
        while True:
            r = s.claim_next(wid)
            if r.get("task"):
                local.append(r["task"]["id"])
                # immediately submit+accept so this worker can grab more
                s.submit(wid, r["task"]["id"], "out")
                s.review(r["task"]["id"], ACCEPTED)
            else:
                break
        with claim_lock:
            claimed.extend(local)

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(drain, workers))

    assert len(claimed) == N, f"claimed {len(claimed)} != {N}"
    assert len(set(claimed)) == N, "a task was claimed more than once!"
    print(f"  claim atomicity OK — {N} tasks, 8 workers, no double-claim")


def test_dependency_gating():
    s = Store()
    s.create_project("deps")
    s.set_state(ACTIVE)
    a = s.add_task("A", "first")
    b = s.add_task("B", "second", depends_on=[a.id])
    w = s.register_worker("alice", "mock").id

    r1 = s.claim_next(w)
    assert r1["task"]["id"] == a.id, "should claim A first"
    # B must be withheld until A is accepted.
    r2 = s.claim_next(w)
    assert r2.get("task") is None and r2.get("wait"), "B leaked before A accepted"

    s.submit(w, a.id, "done A")
    s.review(a.id, ACCEPTED)
    r3 = s.claim_next(w)
    assert r3["task"]["id"] == b.id, "B should be available after A accepted"
    print("  dependency gating OK")


def test_submit_review_followup():
    s = Store()
    s.create_project("review")
    s.set_state(ACTIVE)
    t = s.add_task("thing", "build thing")
    w = s.register_worker("bob", "mock").id
    s.claim_next(w)
    s.submit(w, t.id, "half-done")
    task = s.get_task(t.id)
    assert task.state == SUBMITTED
    reviewed, follow = s.review(t.id, REJECTED, "missing form",
                                followup_instruction="redo with form")
    assert reviewed.state == REJECTED
    assert follow is not None and follow.state == QUEUED
    assert follow.instruction == "redo with form"
    print("  submit/review/follow-up OK")


def test_pause_blocks_claim():
    s = Store()
    s.create_project("pause")
    s.set_state(ACTIVE)
    s.add_task("x", "y")
    w = s.register_worker("carol", "mock").id
    s.set_state(PAUSED)
    r = s.claim_next(w)
    assert r.get("task") is None and r.get("stop"), "paused should tell worker to stop"
    s.set_state(DONE)
    r2 = s.claim_next(w)
    assert r2.get("stop"), "done should tell worker to stop"
    print("  pause/done block claim OK")


def test_stale_requeue():
    """A worker that goes silent has its in-flight task requeued."""
    s = Store()
    s.create_project("stale")
    s.set_state(ACTIVE)
    t = s.add_task("x", "y")
    w = s.register_worker("dave", "mock").id
    r = s.claim_next(w)
    assert r["task"]["id"] == t.id
    # Force last_seen far into the past to simulate a dropout.
    s._workers[w].last_seen = time.time() - (WORKER_TIMEOUT + 5)
    other = s.register_worker("erin", "mock").id
    r2 = s.claim_next(other)  # sweep runs here, requeues t, erin claims it
    assert r2["task"]["id"] == t.id, "stale task was not requeued"
    assert s._workers[w].status == OFFLINE
    print("  stale-claim recovery OK")


if __name__ == "__main__":
    print("=== core/store.py tests ===")
    test_lifecycle_rules()
    test_claim_atomicity()
    test_dependency_gating()
    test_submit_review_followup()
    test_pause_blocks_claim()
    test_stale_requeue()
    print("ALL STORE TESTS PASSED")
