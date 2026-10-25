"""
Thread-safe in-memory store — Agent A owns this (WORK_SPLIT.md §5 Phase 1).

One active project at a time (single room; fine for a hackathon). Everything is
guarded by a single re-entrant lock. No persistence — in-memory is a stated
non-goal to avoid (§9).

The two subtle correctness points this file is responsible for:
  1. claim_next() is ATOMIC — two workers polling concurrently can never claim
     the same task, and a task is only eligible when all its depends_on are
     ACCEPTED.
  2. Stale-claim recovery — a task ASSIGNED to a worker that stopped sending
     heartbeats (last_seen older than WORKER_TIMEOUT) is requeued so the work
     isn't lost when a teammate's laptop drops off the wifi.

A `broadcast` callback can be injected so state changes fan out as SSE events
without the store importing the server.
"""
from __future__ import annotations

import os
import uuid
import threading
from typing import Callable, Optional

from .state import (
    Project, Worker, Task, now,
    PLANNING, ACTIVE, PAUSED, DONE,
    QUEUED, ASSIGNED, SUBMITTED, ACCEPTED, REJECTED,
    IDLE, WORKING, OFFLINE,
)
from .lifecycle import validate_transition

PROVIDER_LABELS = {
    "anthropic": "Claude",
    "openai": "Codex",
    "kimi": "Kimi",
    "custom": "Custom AI",
    "cli": "CLI agent",
    "mock": "Mock",
}

# A worker silent longer than this (seconds) is considered offline; its
# in-flight task is requeued. Heartbeats arrive ~every 5s (§4.3).
WORKER_TIMEOUT = float(os.getenv("CONSORTIUM_WORKER_TIMEOUT", "30"))


def _noop_broadcast(event: str, data: dict) -> None:
    pass


class Store:
    """All mutating ops take the lock; reads that build dicts also take it."""

    def __init__(self, broadcast: Optional[Callable[[str, dict], None]] = None):
        self._lock = threading.RLock()
        self._broadcast = broadcast or _noop_broadcast
        self._project: Optional[Project] = None
        self._tasks: dict[str, Task] = {}
        self._workers: dict[str, Worker] = {}
        self._task_seq = 0

    # ── wiring ──────────────────────────────────────────────────────────────
    def set_broadcast(self, fn: Callable[[str, dict], None]) -> None:
        self._broadcast = fn

    def emit(self, event: str, data: dict) -> None:
        # Called outside the lock by routes after a mutation when they want to
        # push a specific event; internal mutations emit their own.
        self._broadcast(event, data)

    # ── project ─────────────────────────────────────────────────────────────
    def create_project(self, goal: str) -> Project:
        with self._lock:
            pid = "p_" + uuid.uuid4().hex[:4]
            t = now()
            self._project = Project(id=pid, goal=goal, state=PLANNING,
                                    created_at=t, updated_at=t, summary="")
            # Reset everything else for the new project.
            self._tasks.clear()
            self._task_seq = 0
            for w in self._workers.values():
                w.status = IDLE
                w.current_task_id = None
            proj = self._project
        self._broadcast("project_state", {"state": proj.state, "summary": proj.summary})
        return proj

    def get_project(self) -> Optional[Project]:
        with self._lock:
            return self._project

    def set_state(self, target: str) -> Project:
        with self._lock:
            if not self._project:
                raise ValueError("no project")
            validate_transition(self._project.state, target)
            self._project.state = target
            self._project.updated_at = now()
            proj = self._project
            summary = proj.summary
        self._broadcast("project_state", {"state": target, "summary": summary})
        if target == DONE:
            self._broadcast("done", {"summary": summary})
        return proj

    def set_summary(self, summary: str) -> None:
        with self._lock:
            if self._project:
                self._project.summary = summary
                self._project.updated_at = now()

    # ── tasks ───────────────────────────────────────────────────────────────
    def add_task(self, title: str, instruction: str,
                 depends_on: Optional[list[str]] = None,
                 assignee: Optional[str] = None) -> Task:
        with self._lock:
            if not self._project:
                raise ValueError("no project")
            # Only pin to a worker that actually exists; otherwise leave open.
            if assignee and assignee not in self._workers:
                assignee = None
            self._task_seq += 1
            tid = "t_%02d" % self._task_seq
            t = now()
            task = Task(
                id=tid, project_id=self._project.id, seq=self._task_seq,
                title=title, instruction=instruction, state=QUEUED,
                assignee=assignee,
                depends_on=list(depends_on or []), created_at=t, updated_at=t,
            )
            self._tasks[tid] = task
            snap = task.to_dict()
        self._broadcast("task_created", {"task": snap})
        return task

    def assign_task(self, task_id: str, worker_id: Optional[str]) -> Task:
        """Leader pins (or unpins) a QUEUED task to a specific worker. The pinned
        worker will receive it on its next pull, ahead of unpinned tasks; other
        workers will skip it. Passing worker_id=None clears the pin."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise ValueError("unknown task")
            if task.state not in (QUEUED, REJECTED):
                raise ValueError(f"can only assign a queued task (this is {task.state})")
            if worker_id is not None and worker_id not in self._workers:
                raise ValueError("unknown worker")
            task.assignee = worker_id
            task.updated_at = now()
            snap = task.to_dict()
        self._broadcast("task_created", {"task": snap})  # UI re-renders the task
        return task

    def add_tasks_bulk(self, specs: list[dict]) -> list[Task]:
        """Used by /plan. specs: [{title, instruction, depends_on?}].

        INTEGRATION CONTRACT with Agent B's orchestrator.plan() (§6): in the
        returned specs, `depends_on` holds 0-based INDEXES into this same specs
        list (task ids don't exist until we create them here). We map those
        indexes to the real task ids as we go. For resilience we ALSO accept
        entries that are already real task ids (strings matching an id we just
        created), so a caller passing real ids still works.

        Emits one tasks_planned event with the whole batch (cleaner for the UI).
        """
        created: list[Task] = []
        with self._lock:
            if not self._project:
                raise ValueError("no project")
            # First pass: create tasks with placeholder deps, remember the id
            # assigned at each position so we can resolve index deps after.
            position_to_id: list[str] = []
            raw_deps: list[list] = []
            for spec in specs:
                self._task_seq += 1
                tid = "t_%02d" % self._task_seq
                t = now()
                task = Task(
                    id=tid, project_id=self._project.id, seq=self._task_seq,
                    title=spec.get("title", f"Task {self._task_seq}"),
                    instruction=spec.get("instruction", ""), state=QUEUED,
                    depends_on=[], created_at=t, updated_at=t,
                )
                self._tasks[tid] = task
                created.append(task)
                position_to_id.append(tid)
                raw_deps.append(list(spec.get("depends_on") or []))

            # Second pass: resolve deps (index -> id, or pass through real ids).
            created_ids = set(position_to_id)
            for task, deps in zip(created, raw_deps):
                resolved: list[str] = []
                for d in deps:
                    if isinstance(d, bool):
                        continue
                    if isinstance(d, int) and 0 <= d < len(position_to_id):
                        resolved.append(position_to_id[d])
                    elif isinstance(d, str) and d in created_ids:
                        resolved.append(d)
                    # anything else (dangling index/id) is dropped, not fatal
                task.depends_on = resolved

            batch = [t.to_dict() for t in created]
        self._broadcast("tasks_planned", {"tasks": batch})
        return created

    def list_tasks(self, state: Optional[str] = None) -> list[Task]:
        with self._lock:
            tasks = sorted(self._tasks.values(), key=lambda t: t.seq)
            if state:
                tasks = [t for t in tasks if t.state == state]
            return tasks

    def get_task(self, task_id: str) -> Optional[Task]:
        with self._lock:
            return self._tasks.get(task_id)

    def _deps_satisfied(self, task: Task) -> bool:
        for dep_id in task.depends_on:
            dep = self._tasks.get(dep_id)
            if not dep or dep.state != ACCEPTED:
                return False
        return True

    # ── worker registry ────────────────────────────────────────────────────
    def register_worker(self, owner: str, provider: str,
                        api_key: Optional[str] = None,
                        model: Optional[str] = None) -> Worker:
        with self._lock:
            wid = f"{owner.lower().replace(' ', '-')}-{provider}-{uuid.uuid4().hex[:4]}"
            display = f"{owner}'s {PROVIDER_LABELS.get(provider, provider)}"
            w = Worker(id=wid, owner=owner, provider=provider, display=display,
                       status=IDLE, last_seen=now(), current_task_id=None)
            self._workers[wid] = w
            snap = w.to_dict()
        self._broadcast("worker_joined", {"worker": snap})
        return w

    def heartbeat(self, worker_id: str) -> Optional[Worker]:
        with self._lock:
            w = self._workers.get(worker_id)
            if w:
                w.last_seen = now()
                if w.status == OFFLINE:
                    w.status = IDLE
            return w

    def list_workers(self) -> list[Worker]:
        with self._lock:
            self._sweep_stale_locked()
            return list(self._workers.values())

    def workers_online(self) -> int:
        with self._lock:
            self._sweep_stale_locked()
            return sum(1 for w in self._workers.values() if w.status != OFFLINE)

    def remove_worker(self, worker_id: str) -> None:
        requeued: Optional[str] = None
        with self._lock:
            w = self._workers.pop(worker_id, None)
            if w and w.current_task_id:
                requeued = self._requeue_task_locked(w.current_task_id)
            # Release pins to the departing worker so those tasks go to others.
            for t in self._tasks.values():
                if t.assignee == worker_id and t.state == QUEUED:
                    t.assignee = None
                    t.updated_at = now()
        self._broadcast("worker_left", {"worker_id": worker_id})
        if requeued:
            self._broadcast("task_created",
                            {"task": self._tasks[requeued].to_dict()})

    # ── the core pull: atomic claim ─────────────────────────────────────────
    def claim_next(self, worker_id: str) -> dict:
        """Atomically hand this worker the next eligible queued task.

        Returns one of:
          {"task": <dict>, "project": <dict>}   task claimed
          {"task": None, "wait": True}          nothing ready yet (poll again)
          {"task": None, "stop": True}          project paused/done, stop
          {"task": None, "error": "..."}        unknown worker / no project
        """
        with self._lock:
            self._sweep_stale_locked()
            if not self._project:
                return {"task": None, "error": "no project"}
            w = self._workers.get(worker_id)
            if not w:
                return {"task": None, "error": "unknown worker"}
            w.last_seen = now()

            if self._project.state in (PAUSED, DONE):
                return {"task": None, "stop": True}
            if self._project.state != ACTIVE:
                return {"task": None, "wait": True}

            # Eligible = queued, deps satisfied, and not pinned to someone else.
            # Pinned-to-ME tasks win over unpinned ones; within each group the
            # lowest seq goes first. This is how the leader "assigns" a task to
            # a specific worker: it simply pins, and that worker pulls it next.
            def _rank(t):
                if t.assignee == worker_id:
                    return (0, t.seq)   # my pinned tasks first
                return (1, t.seq)       # then open tasks

            candidate = None
            for task in sorted(self._tasks.values(), key=_rank):
                if task.state != QUEUED:
                    continue
                if task.assignee and task.assignee != worker_id:
                    continue  # pinned to a different worker — not for me
                if not self._deps_satisfied(task):
                    continue
                candidate = task
                break

            if candidate is None:
                return {"task": None, "wait": True}

            candidate.state = ASSIGNED
            candidate.assigned_to = worker_id
            candidate.updated_at = now()
            w.status = WORKING
            w.current_task_id = candidate.id
            task_snap = candidate.to_dict()
            proj_snap = self._project.to_dict()

        self._broadcast("task_claimed",
                        {"task_id": task_snap["id"], "worker_id": worker_id})
        return {"task": task_snap, "project": proj_snap}

    def submit(self, worker_id: str, task_id: str, output: str,
               ok: bool = True, error: str = "", elapsed: float = 0.0) -> Task:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise ValueError("unknown task")
            if task.assigned_to != worker_id:
                raise ValueError("task not assigned to this worker")
            task.result = output
            task.state = SUBMITTED
            task.review_note = error if not ok else ""
            task.updated_at = now()
            w = self._workers.get(worker_id)
            if w:
                w.status = IDLE
                w.current_task_id = None
                w.last_seen = now()
            snap_ok = ok
        self._broadcast("task_submitted",
                        {"task_id": task_id, "worker_id": worker_id, "ok": snap_ok})
        return task

    def review(self, task_id: str, decision: str, note: str = "",
               followup_instruction: Optional[str] = None) -> tuple[Task, Optional[Task]]:
        """Accept or reject a submitted task; optionally enqueue a follow-up."""
        if decision not in (ACCEPTED, REJECTED):
            raise ValueError("decision must be 'accepted' or 'rejected'")
        followup: Optional[Task] = None
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise ValueError("unknown task")
            task.state = decision
            task.review_note = note
            task.updated_at = now()
            if decision == REJECTED and followup_instruction:
                self._task_seq += 1
                fid = "t_%02d" % self._task_seq
                t = now()
                followup = Task(
                    id=fid, project_id=task.project_id, seq=self._task_seq,
                    title=f"Redo: {task.title}", instruction=followup_instruction,
                    state=QUEUED, depends_on=list(task.depends_on),
                    created_at=t, updated_at=t,
                )
                self._tasks[fid] = followup
                # Rewire: any task that depended on the REJECTED task must now
                # depend on its follow-up instead, or it would be gated forever
                # (the rejected id can never reach ACCEPTED). Flagged by Agent B.
                for other in self._tasks.values():
                    if other.id in (task.id, fid):
                        continue
                    if task.id in other.depends_on:
                        other.depends_on = [fid if d == task.id else d
                                            for d in other.depends_on]
                        other.updated_at = now()
            task_snap = task.to_dict()
            follow_snap = followup.to_dict() if followup else None
            follow_id = followup.id if followup else None
        self._broadcast("task_reviewed", {
            "task_id": task_id, "decision": decision, "note": note,
            "followup_id": follow_id,
        })
        if follow_snap:
            self._broadcast("task_created", {"task": follow_snap})
        return task, followup

    # ── stale-claim recovery ────────────────────────────────────────────────
    def _requeue_task_locked(self, task_id: str) -> Optional[str]:
        task = self._tasks.get(task_id)
        if task and task.state == ASSIGNED:
            task.state = QUEUED
            task.assigned_to = None
            task.updated_at = now()
            return task_id
        return None

    def _sweep_stale_locked(self) -> None:
        """Mark silent workers offline and requeue their in-flight tasks.
        Caller must already hold the lock."""
        cutoff = now() - WORKER_TIMEOUT
        for w in self._workers.values():
            if w.status != OFFLINE and w.last_seen < cutoff:
                w.status = OFFLINE
                if w.current_task_id:
                    self._requeue_task_locked(w.current_task_id)
                    w.current_task_id = None
                # Release any pins to this offline worker so the work isn't
                # stranded — another worker can then pull those tasks.
                for t in self._tasks.values():
                    if t.assignee == w.id and t.state == QUEUED:
                        t.assignee = None
                        t.updated_at = now()

    # ── aggregate view ─────────────────────────────────────────────────────
    def stats(self) -> dict:
        with self._lock:
            self._sweep_stale_locked()
            counts = {QUEUED: 0, ASSIGNED: 0, SUBMITTED: 0, ACCEPTED: 0, REJECTED: 0}
            for t in self._tasks.values():
                counts[t.state] = counts.get(t.state, 0) + 1
            counts["workers_online"] = sum(
                1 for w in self._workers.values() if w.status != OFFLINE)
            return counts

    def snapshot(self) -> dict:
        """Full state for the SSE `snapshot` event (§4.4)."""
        with self._lock:
            self._sweep_stale_locked()
            proj = self._project.to_dict() if self._project else None
            tasks = [t.to_dict() for t in sorted(self._tasks.values(),
                                                  key=lambda t: t.seq)]
            workers = [w.to_dict() for w in self._workers.values()]
            counts = {QUEUED: 0, ASSIGNED: 0, SUBMITTED: 0, ACCEPTED: 0, REJECTED: 0}
            for t in self._tasks.values():
                counts[t.state] = counts.get(t.state, 0) + 1
            counts["workers_online"] = sum(
                1 for w in self._workers.values() if w.status != OFFLINE)
        return {"project": proj, "tasks": tasks, "workers": workers, "stats": counts}
