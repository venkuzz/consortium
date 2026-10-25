"""
The brain: turns the leader's goal into tasks, reviews worker results, and keeps a
rolling summary. This module is the ONLY import across the Agent A / Agent B
boundary (WORK_SPLIT.md §6). Every function degrades to deterministic output when
the Lead AI is unavailable or returns junk — the demo never dies on one bad call.

Note for the server: in `plan()` output, `depends_on` holds 0-based INDEXES into the
returned `tasks` list (task ids don't exist yet). Map them to the created task ids.
"""
from __future__ import annotations

import json
import re
from typing import Optional

from lead import prompts
from lead.lead_ai import get_lead

MAX_TASKS = 8
ACCEPTED, REJECTED = "accepted", "rejected"


# ---------- JSON helpers ----------

def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of a model reply; tolerate ``` fences and prose."""
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    start = cleaned.find("{")
    if start < 0:
        raise ValueError("no JSON object in Lead AI output")
    obj, _ = json.JSONDecoder().raw_decode(cleaned[start:])
    if not isinstance(obj, dict):
        raise ValueError("Lead AI JSON is not an object")
    return obj


def _normalize_tasks(raw) -> list[dict]:
    """Validate a task list; drop bad entries, keep only backward dependencies (no cycles)."""
    if not isinstance(raw, list):
        raise ValueError("'tasks' is not a list")
    tasks: list[dict] = []
    index_map: dict[int, int] = {}  # original index -> kept index
    for i, t in enumerate(raw):
        if len(tasks) >= MAX_TASKS:
            break
        if not isinstance(t, dict):
            continue
        title = str(t.get("title") or "").strip()
        instruction = str(t.get("instruction") or "").strip()
        if not instruction:
            continue
        deps = []
        for d in t.get("depends_on") or []:
            try:
                d = int(d)
            except (TypeError, ValueError):
                continue
            if d in index_map and index_map[d] not in deps:
                deps.append(index_map[d])
        index_map[i] = len(tasks)
        tasks.append({"title": (title or instruction)[:80], "instruction": instruction,
                      "depends_on": deps})
    if not tasks:
        raise ValueError("Lead AI produced no usable tasks")
    return tasks


# ---------- plan ----------

def _template_plan(goal: str) -> list[dict]:
    """Deterministic fallback: a parallel build of one web deliverable + integration."""
    shared = ("Shared conventions: page sections use ids #hero, #features, #signup, #footer; "
              "buttons use class .btn; cards use class .card; the signup form uses "
              "#signup-form, #email and #join.")
    return [
        {"title": "Page structure (HTML)",
         "instruction": f"Write the semantic HTML body markup for: {goal}\n{shared}\n"
                        "Return only HTML (no <style>, no <script>).",
         "depends_on": []},
        {"title": "Styling (CSS)",
         "instruction": f"Write a polished, responsive stylesheet for: {goal}\n{shared}\n"
                        "Return only a single <style> block.",
         "depends_on": []},
        {"title": "Copy & content",
         "instruction": f"Write the user-facing text for: {goal}\n{shared}\n"
                        "Return a headline, a subheadline, three feature blurbs and "
                        "button labels as a short labelled list.",
         "depends_on": []},
        {"title": "Interactivity (JS)",
         "instruction": f"Write vanilla JavaScript for: {goal}\n{shared}\n"
                        "Validate #email on submit of #signup-form and show a thank-you "
                        "message. Return only a single <script> block.",
         "depends_on": []},
        {"title": "Integrate final page",
         "instruction": f"Assemble ONE complete standalone HTML document for: {goal}\n"
                        "Combine the HTML, CSS, copy and JS from the earlier tasks, "
                        "replacing placeholder text with the written copy. "
                        "Return only the full HTML document.",
         "depends_on": [0, 1, 2, 3]},
    ]


def plan(goal: str) -> dict:
    """-> {'tasks': [{'title','instruction','depends_on'}], 'used_llm': bool, 'note': str}"""
    goal = (goal or "").strip()
    lead = get_lead()
    if not lead.available:
        tasks = _template_plan(goal)
        return {"tasks": tasks, "used_llm": False,
                "note": f"No Lead AI configured; used template split ({len(tasks)} tasks)."}
    try:
        reply = lead.complete(prompts.PLAN_SYSTEM, prompts.plan_user(goal))
        tasks = _normalize_tasks(_extract_json(reply).get("tasks"))
        return {"tasks": tasks, "used_llm": True,
                "note": f"Lead AI ({lead.label}) generated {len(tasks)} tasks."}
    except Exception as e:
        tasks = _template_plan(goal)
        return {"tasks": tasks, "used_llm": False,
                "note": f"Lead AI failed ({str(e)[:160]}); used template split "
                        f"({len(tasks)} tasks)."}


# ---------- review ----------

def _retry_instruction(task: dict, reason: str) -> str:
    return (f"{task.get('instruction', '')}\n\n"
            f"(Retry: the previous attempt was rejected — {reason})")


def review(task: dict, result: str, goal: str = "") -> dict:
    """-> {'decision': 'accepted'|'rejected', 'note': str, 'followup_instruction': str|None}

    Empty results are always rejected with a retry. Otherwise the Lead AI judges;
    if it is unavailable or returns junk, the result is accepted."""
    result = result or ""
    if not result.strip():
        note = "Empty result."
        return {"decision": REJECTED, "note": note,
                "followup_instruction": _retry_instruction(task, note)}
    lead = get_lead()
    if not lead.available:
        return {"decision": ACCEPTED, "note": "Auto-accepted (no Lead AI configured).",
                "followup_instruction": None}
    try:
        reply = lead.complete(
            prompts.REVIEW_SYSTEM,
            prompts.review_user(goal, task.get("title", ""), task.get("instruction", ""), result),
            max_tokens=1500)
        obj = _extract_json(reply)
        decision = str(obj.get("decision", "")).strip().lower()
        if decision not in (ACCEPTED, REJECTED):
            raise ValueError(f"bad decision {decision!r}")
        note = str(obj.get("note") or "").strip()[:500]
        followup: Optional[str] = None
        if decision == REJECTED:
            followup = str(obj.get("followup_instruction") or "").strip() or \
                _retry_instruction(task, note or "did not meet the instruction")
        return {"decision": decision, "note": note, "followup_instruction": followup}
    except Exception as e:
        return {"decision": ACCEPTED,
                "note": f"Auto-accepted (Lead AI review failed: {str(e)[:120]}).",
                "followup_instruction": None}


# ---------- summarize ----------

def _count_summary(tasks: list[dict]) -> str:
    counts: dict[str, int] = {}
    for t in tasks:
        counts[t.get("state", "queued")] = counts.get(t.get("state", "queued"), 0) + 1
    total = len(tasks)
    if not total:
        return "No tasks yet."
    done = counts.get(ACCEPTED, 0)
    parts = [f"{done}/{total} tasks accepted"]
    for state in ("assigned", "submitted", "queued", REJECTED):
        if counts.get(state):
            parts.append(f"{counts[state]} {state}")
    latest = [t.get("title", "") for t in sorted(tasks, key=lambda t: t.get("seq", 0))
              if t.get("state") == ACCEPTED][-2:]
    text = ", ".join(parts) + "."
    if latest:
        text += " Latest done: " + "; ".join(latest) + "."
    return text


def summarize(project: dict, tasks: list[dict], use_llm: bool = False) -> str:
    """-> short progress summary string.

    Deterministic by default (cheap; safe to call on every event). Pass use_llm=True
    for a Lead-AI-written summary, which falls back to the deterministic one."""
    base = _count_summary(tasks or [])
    lead = get_lead()
    if not use_llm or not lead.available or not tasks:
        return base
    try:
        lines = [f"#{t.get('seq', '?')} [{t.get('state')}] {t.get('title')}" for t in tasks]
        return lead.complete(prompts.SUMMARY_SYSTEM,
                             prompts.summary_user((project or {}).get("goal", ""), lines),
                             max_tokens=300)[:600]
    except Exception:
        return base
