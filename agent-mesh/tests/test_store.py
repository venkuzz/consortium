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


def test_bulk_index_deps_resolved():
    """Agent B's orchestrator.plan() returns depends_on as 0-based INDEXES into
    the task list (§6). add_tasks_bulk must map them to real task ids."""
    s = Store()
    s.create_project("plan deps")
    specs = [
        {"title": "HTML", "instruction": "html", "depends_on": []},
        {"title": "CSS", "instruction": "css", "depends_on": []},
        {"title": "Copy", "instruction": "copy", "depends_on": []},
        {"title": "JS", "instruction": "js", "depends_on": []},
        # the integrate task depends on the first four BY INDEX
        {"title": "Integrate", "instruction": "assemble", "depends_on": [0, 1, 2, 3]},
    ]
    tasks = s.add_tasks_bulk(specs)
    integrate = tasks[-1]
    first_four_ids = [t.id for t in tasks[:4]]
    assert integrate.depends_on == first_four_ids, \
        f"index deps not resolved to ids: {integrate.depends_on}"
    # And they must actually gate: integrate shouldn't be claimable until the
    # first four are accepted.
    s.set_state(ACTIVE)
    w = s.register_worker("z", "mock").id
    claimed = []
    for _ in range(4):
        r = s.claim_next(w)
        assert r["task"]["id"] in first_four_ids
        claimed.append(r["task"]["id"])
        s.submit(w, r["task"]["id"], "out")
        s.review(r["task"]["id"], ACCEPTED)
    r = s.claim_next(w)
    assert r["task"]["id"] == integrate.id, "integrate should unlock after deps accepted"
    print("  bulk index-dep resolution OK (Agent B /plan contract)")


def test_rejection_rewires_dependents():
    """A task depending on a rejected task must be rewired to its follow-up,
    otherwise it is gated forever (flagged by Agent B's HANDOFF #2)."""
    s = Store()
    s.create_project("rewire")
    s.set_state(ACTIVE)
    a = s.add_task("A", "first")
    b = s.add_task("B", "depends on A", depends_on=[a.id])
    w = s.register_worker("alice", "mock").id

    # Claim + submit A, then REJECT with a follow-up.
    s.claim_next(w)
    s.submit(w, a.id, "bad output")
    _, follow = s.review(a.id, REJECTED, "redo", followup_instruction="do it properly")
    assert follow is not None

    # B must now depend on the follow-up, not the dead rejected id.
    b_after = s.get_task(b.id)
    assert follow.id in b_after.depends_on, "B was not rewired to the follow-up"
    assert a.id not in b_after.depends_on, "B still points at the rejected task"

    # B must not be claimable yet (follow-up not accepted).
    r = s.claim_next(w)
    assert r["task"]["id"] == follow.id, "should claim the follow-up next"
    s.submit(w, follow.id, "good output")
    s.review(follow.id, ACCEPTED)

    # Now B unblocks.
    r2 = s.claim_next(w)
    assert r2["task"]["id"] == b.id, "B did not unblock after follow-up accepted"
    print("  rejection rewires dependents OK (Agent B HANDOFF #2)")


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
    test_bulk_index_deps_resolved()
    test_rejection_rewires_dependents()
    test_stale_requeue()
    print("ALL STORE TESTS PASSED")
