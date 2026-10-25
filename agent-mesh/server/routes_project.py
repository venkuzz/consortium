"""
Project + task route handlers — Agent A (WORK_SPLIT.md §4.2).

Each handler takes (store, body, params) and returns (status_code, dict).
`params` holds path captures like the task id. These are pure functions of the
store so they're trivially testable; app.py maps URLs to them.
"""
from __future__ import annotations

import json
import re
import time

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


def _slug(value: str) -> str:
    """Turn a project goal into a short, portable filename stem."""
    stem = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return (stem[:60].rstrip("-") or "consortium-result")


def _strip_outer_fence(value: str) -> str:
    """Remove one Markdown code fence without changing the deliverable inside."""
    text = value.strip()
    match = re.fullmatch(r"```[^\n]*\n([\s\S]*?)\n?```", text)
    return match.group(1).strip() if match else text


def _artifact_type(content: str) -> tuple[str, str]:
    """Infer a useful extension and MIME type from a worker's final output."""
    sample = content.lstrip()
    lowered = sample[:1000].lower()
    if ("<!doctype html" in lowered or "<html" in lowered or
            ("<head" in lowered and "<body" in lowered) or
            re.match(r"<[a-z][\w-]*(?:\s|>)", sample, re.I)):
        return ".html", "text/html;charset=utf-8"
    if sample.startswith(("{", "[")):
        try:
            json.loads(sample)
            return ".json", "application/json;charset=utf-8"
        except json.JSONDecodeError:
            pass
    if re.search(r"(^|\n)\s*(const|let|var|function|export|import)\s", sample):
        return ".js", "text/javascript;charset=utf-8"
    if ("{" in sample and "}" in sample and
            re.search(r"(^|\n)\s*([.#][\w-]+|[a-z][\w-]*)[^\n{]*\{", sample, re.I)):
        return ".css", "text/css;charset=utf-8"
    if re.search(r"(^|\n)\s*(#{1,6}\s|[-*]\s|\d+\.\s|>\s|```)", sample):
        return ".md", "text/markdown;charset=utf-8"
    return ".txt", "text/plain;charset=utf-8"


def _final_task(tasks):
    """Choose the accepted output most likely to be the integrated deliverable."""
    candidates = [task for task in tasks
                  if task.state == ACCEPTED and (task.result or "").strip()]
    if not candidates:
        return None
    dependency_ids = {dep for task in tasks for dep in task.depends_on}
    final_words = re.compile(
        r"\b(final|integrat(?:e|ed|ion)|assembl(?:e|ed|y)|complete|deliverable|ship|build)\b",
        re.I,
    )

    def score(task):
        # Integration wording and leaf position are stronger signals than raw
        # task order; dependency count breaks ties in favor of aggregator work.
        return (
            100 if final_words.search(task.title) else 0,
            30 if task.id not in dependency_ids else 0,
            len(task.depends_on) * 10,
            task.seq,
        )

    return max(candidates, key=score)


# GET /api/project/export?format=artifact|record
def export_project(store, body, params):
    project = store.get_project()
    if not project:
        return 404, {"error": "Create a project before exporting."}

    export_format = (params.get("format") or "artifact").strip().lower()
    if export_format not in ("artifact", "record"):
        return 400, {"error": "format must be 'artifact' or 'record'"}

    tasks = store.list_tasks()
    final_task = _final_task(tasks)
    stem = _slug(project.goal)

    if export_format == "record":
        record = {
            "schema": "consortium.project.v1",
            "exported_at": round(time.time(), 3),
            "project": project.to_dict(),
            "stats": store.stats(),
            "tasks": [task.to_dict() for task in tasks],
            "final_task_id": final_task.id if final_task else None,
        }
        return 200, {
            "filename": f"{stem}-consortium.json",
            "mime_type": "application/json;charset=utf-8",
            "content": json.dumps(record, indent=2, ensure_ascii=False),
            "source_task": final_task.to_dict() if final_task else None,
            "accepted_count": sum(task.state == ACCEPTED for task in tasks),
            "total_tasks": len(tasks),
        }

    if not final_task:
        return 409, {
            "error": "Accept at least one task result before exporting the final result."
        }

    content = _strip_outer_fence(final_task.result)
    extension, mime_type = _artifact_type(content)
    return 200, {
        "filename": f"{stem}{extension}",
        "mime_type": mime_type,
        "content": content,
        "source_task": {
            "id": final_task.id,
            "title": final_task.title,
            "seq": final_task.seq,
        },
        "accepted_count": sum(task.state == ACCEPTED for task in tasks),
        "total_tasks": len(tasks),
    }


# POST /api/project   {goal}
def create_project(store, body, params):
    goal = (body.get("goal") or "").strip()
    if not goal:
        return 400, {"error": "goal required"}
    proj = store.create_project(goal)
    return 200, {"project": proj.to_dict()}


# POST /api/project/plan   {} or {goal, lead_provider?, lead_api_key?, lead_model?, lead_base_url?}
def plan_project(store, body, params):
    proj = store.get_project()
    if not proj:
        return 400, {"error": "no project; POST /api/project first"}
    goal = (body.get("goal") or proj.goal).strip()
    if body.get("goal"):
        # allow overriding the stored goal
        proj.goal = goal

    # Let the leader choose/supply their AI for this plan (optional). A blank
    # provider/key falls back to the server's env-configured Lead AI, which
    # falls back to the deterministic template — so /plan always works.
    lead_note = ""
    if any(body.get(k) for k in ("lead_provider", "lead_api_key", "lead_model", "lead_base_url")):
        cfg = lead_facade.configure_lead(
            provider=(body.get("lead_provider") or "").strip() or None,
            api_key=(body.get("lead_api_key") or "").strip() or None,
            model=(body.get("lead_model") or "").strip() or None,
            base_url=(body.get("lead_base_url") or "").strip() or None,
        )
        lead_note = cfg.get("note", "")

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
        "note": (lead_note + " " if lead_note else "") + result.get("note", ""),
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
        message = str(e)
        return (404 if message == "unknown task" else 409), {"error": message}
    # Refresh rolling summary after a review.
    all_tasks = [t.to_dict() for t in store.list_tasks()]
    proj = store.get_project()
    if proj:
        store.set_summary(lead_facade.summarize(proj.to_dict(), all_tasks))
    return 200, {
        "task": task.to_dict(),
        "followup": follow.to_dict() if follow else None,
    }
