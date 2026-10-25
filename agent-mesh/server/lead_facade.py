"""
Lead facade resolver — Agent A (WORK_SPLIT.md §6).

The server calls only three functions from Agent B's brain:
    plan(goal) -> {'tasks':[{title,instruction,depends_on}], 'used_llm', 'note'}
    review(task, result) -> {'decision','note','followup_instruction'|None}
    summarize(project, tasks) -> str

If Agent B's `lead/orchestrator.py` is merged and importable, we use it.
Otherwise we fall back to a deterministic stub so the server runs standalone
(explicitly allowed by §6). This keeps Agent A unblocked by Agent B.
"""
from __future__ import annotations

try:  # prefer the real brain once Agent B merges it
    from lead.orchestrator import plan as _plan          # type: ignore
    from lead.orchestrator import review as _review      # type: ignore
    from lead.orchestrator import summarize as _summarize  # type: ignore
    USING_REAL_LEAD = True
except Exception:  # noqa: BLE001 — any import failure => fall back
    USING_REAL_LEAD = False

    def _plan(goal: str) -> dict:
        """Deterministic template split so the demo always runs."""
        tasks = [
            {"title": "Define scope",
             "instruction": f"Write a one-paragraph scope and success criteria "
                            f"for: {goal}", "depends_on": []},
            {"title": "Build core",
             "instruction": f"Implement the core deliverable for: {goal}. "
                            f"Use clear, conventional structure.", "depends_on": []},
            {"title": "Polish & review",
             "instruction": f"Review and polish the core deliverable for: {goal}.",
             "depends_on": []},
        ]
        return {"tasks": tasks, "used_llm": False,
                "note": "Lead AI unavailable; used deterministic template split."}

    def _review(task: dict, result: str) -> dict:
        ok = bool(result and result.strip())
        return {
            "decision": "accepted" if ok else "rejected",
            "note": "auto-accepted (stub lead)" if ok else "empty result",
            "followup_instruction": None if ok else
                f"Redo and produce real output for: {task.get('title', '')}",
        }

    def _summarize(project: dict, tasks: list[dict]) -> str:
        total = len(tasks)
        accepted = sum(1 for t in tasks if t.get("state") == "accepted")
        return f"{accepted}/{total} tasks accepted."


def plan(goal: str) -> dict:
    return _plan(goal)


def review(task: dict, result: str) -> dict:
    return _review(task, result)


def summarize(project: dict, tasks: list[dict]) -> str:
    return _summarize(project, tasks)
