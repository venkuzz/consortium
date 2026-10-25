"""
Lead facade resolver — Agent A (WORK_SPLIT.md §6).

The server calls only three functions from Agent B's brain:
    plan(goal) -> {'tasks':[{title,instruction,depends_on}], 'used_llm', 'note'}
    review(task, result) -> {'decision','note','followup_instruction'|None}
    summarize(project, tasks) -> str

Resolution order (first that imports wins):
  1. `lead.orchestrator`                   — Agent B merged under agent-mesh/.
  2. `consortium-leader-worker/lead/`       — Agent B merged under that dir
                                              (the §2 tree path); loaded by path.
  3. deterministic stub                     — nothing merged yet; keeps the
                                              server standalone (allowed by §6).

This indirection exists because the two agents started from different roots
(canonical plan is under agent-mesh/, Agent B's branch used
consortium-leader-worker/). Supporting both means integration works whichever
way their branch lands, WITHOUT Agent A editing Agent B's files.

NOTE on the plan() contract: depends_on in plan() output are 0-based INDEXES
into the tasks list. The mapping to real task ids is handled by
core.store.add_tasks_bulk, not here.
"""
from __future__ import annotations

import os
import sys
import importlib.util

USING_REAL_LEAD = False
_plan = None
_review = None
_summarize = None


def _load_real_lead():
    """Try the two possible locations of Agent B's orchestrator."""
    global _plan, _review, _summarize, USING_REAL_LEAD

    # 1. Normal import (agent-mesh/lead on sys.path).
    try:
        from lead.orchestrator import plan as p, review as r, summarize as s  # type: ignore
        _plan, _review, _summarize = p, r, s
        USING_REAL_LEAD = True
        return
    except Exception:  # noqa: BLE001
        pass

    # 2. Path-load from the sibling consortium-leader-worker/ tree, if present.
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # agent-mesh/
    repo_root = os.path.dirname(here)
    clw = os.path.join(repo_root, "consortium-leader-worker")
    orch_path = os.path.join(clw, "lead", "orchestrator.py")
    if os.path.isfile(orch_path):
        try:
            if clw not in sys.path:
                sys.path.insert(0, clw)  # so `from lead import ...` inside it works
            spec = importlib.util.spec_from_file_location("lead.orchestrator", orch_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore
            _plan, _review, _summarize = mod.plan, mod.review, mod.summarize
            USING_REAL_LEAD = True
            return
        except Exception:  # noqa: BLE001
            pass


_load_real_lead()


# ── deterministic stub (used only if the real lead could not be loaded) ──────
def _stub_plan(goal: str) -> dict:
    tasks = [
        {"title": "Define scope",
         "instruction": f"Write a one-paragraph scope and success criteria for: {goal}",
         "depends_on": []},
        {"title": "Build core",
         "instruction": f"Implement the core deliverable for: {goal}.",
         "depends_on": []},
        {"title": "Polish & review",
         "instruction": f"Review and polish the core deliverable for: {goal}.",
         "depends_on": [0, 1]},
    ]
    return {"tasks": tasks, "used_llm": False,
            "note": "Lead AI unavailable; used deterministic template split."}


def _stub_review(task: dict, result: str) -> dict:
    ok = bool(result and result.strip())
    return {
        "decision": "accepted" if ok else "rejected",
        "note": "auto-accepted (stub lead)" if ok else "empty result",
        "followup_instruction": None if ok else
            f"Redo and produce real output for: {task.get('title', '')}",
    }


def _stub_summarize(project: dict, tasks: list[dict]) -> str:
    total = len(tasks)
    accepted = sum(1 for t in tasks if t.get("state") == "accepted")
    return f"{accepted}/{total} tasks accepted."


def plan(goal: str) -> dict:
    return (_plan or _stub_plan)(goal)


def review(task: dict, result: str) -> dict:
    fn = _review or _stub_review
    try:
        return fn(task, result)
    except TypeError:
        # Agent B's review(task, result, goal="") takes an extra optional arg.
        return fn(task, result, "")  # type: ignore


def summarize(project: dict, tasks: list[dict]) -> str:
    return (_summarize or _stub_summarize)(project, tasks)
