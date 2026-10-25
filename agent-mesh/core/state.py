"""
Consortium core data model — FROZEN per WORK_SPLIT.md §3.

Agent A owns this file. Agent B and the frontend code against these exact
field names. Do NOT rename a field without a PR that updates §3 of the plan.

Stdlib only — no third-party deps (same constraint as agent-mesh).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, asdict
from typing import Optional

# ── Project lifecycle states ────────────────────────────────────────────────
PLANNING = "planning"   # leader is defining the goal; no tasks dispatched yet
ACTIVE = "active"       # tasks are being pulled/executed
PAUSED = "paused"       # leader halted dispatch; workers idle
DONE = "done"           # leader declared completion; workers stop

PROJECT_STATES = {PLANNING, ACTIVE, PAUSED, DONE}

# ── Task lifecycle states ───────────────────────────────────────────────────
QUEUED = "queued"        # created, not yet claimed
ASSIGNED = "assigned"    # claimed by a worker, in progress
SUBMITTED = "submitted"  # worker returned a result, awaiting lead review
ACCEPTED = "accepted"    # lead accepted the result
REJECTED = "rejected"    # lead rejected; may spawn a follow-up task

TASK_STATES = {QUEUED, ASSIGNED, SUBMITTED, ACCEPTED, REJECTED}

# ── Worker status values ────────────────────────────────────────────────────
IDLE = "idle"
WORKING = "working"
OFFLINE = "offline"


def now() -> float:
    """Single clock source so timestamps are consistent across the store."""
    return time.time()


@dataclass
class Project:
    id: str
    goal: str                 # leader's high-level objective
    state: str                # one of PLANNING/ACTIVE/PAUSED/DONE
    created_at: float
    updated_at: float
    summary: str = ""         # lead AI's rolling summary of progress

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Worker:
    id: str                   # "alice-claude-1a2b"
    owner: str                # "Alice"
    provider: str             # "anthropic"|"openai"|"kimi"|"mock"
    display: str              # "Alice's Claude"
    status: str               # "idle"|"working"|"offline"
    last_seen: float          # epoch; used to detect dropouts
    current_task_id: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Task:
    id: str
    project_id: str
    seq: int                  # monotonic ordering for the UI
    title: str                # short label
    instruction: str          # full instruction text from the Lead AI
    state: str                # QUEUED/ASSIGNED/SUBMITTED/ACCEPTED/REJECTED
    assigned_to: Optional[str] = None   # worker id that CLAIMED it (set at claim)
    assignee: Optional[str] = None      # worker id the leader PINNED it to (preference)
    result: str = ""          # worker's submitted output
    review_note: str = ""     # lead's note on accept/reject
    depends_on: list[str] = field(default_factory=list)  # task ids
    created_at: float = 0.0
    updated_at: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)
