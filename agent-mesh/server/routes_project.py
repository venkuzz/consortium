"""
Project + task route handlers — Agent A (WORK_SPLIT.md §4.2).

Each handler takes (store, body, params) and returns (status_code, dict).
`params` holds path captures like the task id. These are pure functions of the
store so they're trivially testable; app.py maps URLs to them.
"""
from __future__ import annotations

from core.state import ACTIVE, DONE, PLANNING, ACCEPTED, REJECTED
from core.lifecycle import TransitionError
from . import lead_facade


def _project_payload(store) -> dict:
    proj = store.get_project()
    return {
        "project": proj.to_dict() if proj else None,
        "stats": store.stats() if proj else {},
    }


# GET /api/project
def get_project(store, body, params):
    return 200, _project_payload(store)


# POST /api/project   {goal}
def create_project(store, body, params):
    goal = (body.get("goal") or "").strip()
    if not goal:
        return 400, {"error": "goal required"}
    proj = store.create_project(goal)
    return 200, {"project": proj.to_dict()}


# POST /api/project/plan   {} or {goal}
def plan_project(store, body, params):
    proj = store.get_project()
    if not proj:
        return 400, {"error": "no project; POST /api/project first"}
    goal = (body.get("goal") or proj.goal).strip()
    if body.get("goal"):
        # allow overriding the stored goal
        proj.goal = goal

    result = lead_facade.plan(goal)
    specs = result.get("tasks", [])
    tasks = store.add_tasks_bulk(specs)

    # Move to active (planning->active is allowed; no-op if already active).
    try:
        if proj.state == PLANNING:
            store.set_state(ACTIVE)
    except TransitionError:
        pass

    # Rolling summary.
    all_tasks = [t.to_dict() for t in store.list_tasks()]
    store.set_summary(lead_facade.summarize(store.get_project().to_dict(), all_tasks))

    return 200, {
        "project": store.get_project().to_dict(),
        "tasks": [t.to_dict() for t in tasks],
        "used_llm": result.get("used_llm", False),
        "note": result.get("note", ""),
    }


# POST /api/project/state   {state}
def set_state(store, body, params):
    if not store.get_project():
        return 400, {"error": "no project"}
    target = (body.get("state") or "").strip()
    try:
        proj = store.set_state(target)
    except TransitionError as e:
        return 409, {"error": str(e)}
    except ValueError as e:
        return 400, {"error": str(e)}
    return 200, {"project": proj.to_dict()}


# GET /api/tasks?state=
def list_tasks(store, body, params):
    state = (params.get("state") or "").strip() or None
    tasks = store.list_tasks(state)
    return 200, {"tasks": [t.to_dict() for t in tasks]}


# POST /api/tasks   {title, instruction, depends_on?, assignee?}
def add_task(store, body, params):
    if not store.get_project():
        return 400, {"error": "no project"}
    title = (body.get("title") or "").strip()
    instruction = (body.get("instruction") or "").strip()
    if not title or not instruction:
        return 400, {"error": "title and instruction required"}
    assignee = (body.get("assignee") or "").strip() or None
    task = store.add_task(title, instruction, body.get("depends_on") or [], assignee)
    return 200, {"task": task.to_dict()}


# POST /api/tasks/{id}/assign   {worker_id}  (worker_id null/"" clears the pin)
def assign_task(store, body, params):
    task_id = params.get("id")
    worker_id = (body.get("worker_id") or "").strip() or None
    try:
        task = store.assign_task(task_id, worker_id)
    except ValueError as e:
        return 400, {"error": str(e)}
    return 200, {"task": task.to_dict()}


# POST /api/tasks/{id}/review   {decision, note, followup_instruction?}
def review_task(store, body, params):
    task_id = params.get("id")
    decision = (body.get("decision") or "").strip()
    if decision not in (ACCEPTED, REJECTED):
        return 400, {"error": "decision must be 'accepted' or 'rejected'"}
    try:
        task, follow = store.review(
            task_id, decision,
            note=body.get("note", ""),
            followup_instruction=body.get("followup_instruction"),
        )
    except ValueError as e:
        return 404, {"error": str(e)}
    # Refresh rolling summary after a review.
    all_tasks = [t.to_dict() for t in store.list_tasks()]
    proj = store.get_project()
    if proj:
        store.set_summary(lead_facade.summarize(proj.to_dict(), all_tasks))
    return 200, {
        "task": task.to_dict(),
        "followup": follow.to_dict() if follow else None,
    }
