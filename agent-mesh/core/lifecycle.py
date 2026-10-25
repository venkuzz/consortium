"""
Project lifecycle state machine — Agent A owns this (WORK_SPLIT.md §4.2).

Validates project state transitions so routes don't have to. Keeps the rules
in one place:

  planning → active   (normally via /plan; allowed directly if tasks exist)
  active   ⇄ paused
  any      → done     (terminal; workers must stop pulling)
  done     → (nothing; restart via POST /api/project which makes a new project)
"""
from __future__ import annotations

from .state import PLANNING, ACTIVE, PAUSED, DONE, PROJECT_STATES

# Allowed transitions as {from_state: {to_states}}.
_ALLOWED: dict[str, set[str]] = {
    PLANNING: {ACTIVE, DONE},
    ACTIVE: {PAUSED, DONE},
    PAUSED: {ACTIVE, DONE},
    DONE: set(),  # terminal
}


class TransitionError(Exception):
    """Raised when a requested project state change is not permitted."""


def can_transition(current: str, target: str) -> bool:
    if target not in PROJECT_STATES:
        return False
    return target in _ALLOWED.get(current, set())


def validate_transition(current: str, target: str) -> None:
    """Raise TransitionError if current→target is not allowed."""
    if target not in PROJECT_STATES:
        raise TransitionError(f"unknown project state: {target!r}")
    if current == target:
        return  # idempotent no-op is harmless
    if not can_transition(current, target):
        raise TransitionError(f"cannot move project from {current!r} to {target!r}")
