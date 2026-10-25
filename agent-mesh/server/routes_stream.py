"""
SSE stream route — Agent A (WORK_SPLIT.md §4.4).

The actual streaming lives in app.Handler._stream_events (it needs direct
access to the wire to push frames incrementally). This module documents the
event contract and exposes the snapshot builder for reuse/testing.

Event types pushed on GET /api/events:
    snapshot        {project, tasks, workers, stats}   (once, on connect)
    project_state   {state, summary}
    tasks_planned   {tasks:[...]}
    task_created    {task}
    task_claimed    {task_id, worker_id}
    task_submitted  {task_id, worker_id, ok}
    task_reviewed   {task_id, decision, note, followup_id?}
    worker_joined   {worker}
    worker_left     {worker_id}
    done            {summary}
"""
from __future__ import annotations

EVENT_TYPES = {
    "snapshot", "project_state", "tasks_planned", "task_created",
    "task_claimed", "task_submitted", "task_reviewed",
    "worker_joined", "worker_left", "done",
}


def build_snapshot(store) -> dict:
    """Full state payload for the initial `snapshot` event."""
    return store.snapshot()
