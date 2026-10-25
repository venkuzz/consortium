"""
Lead AI review loop: the Lead AI acting "on the leader's behalf" (WORK_SPLIT.md §4.2).

Runs next to the server on the leader's machine. Uses only the public §4 API:
polls GET /api/tasks?state=submitted, judges each result with
`orchestrator.review()`, and POSTs /api/tasks/{id}/review. With --auto-done it also
marks the project done once every task is accepted.

  python -m lead.lead_agent --server http://127.0.0.1:8000
  LEAD_PROVIDER=anthropic python -m lead.lead_agent --server http://127.0.0.1:8000 --auto-done
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lead import lead_ai, orchestrator  # noqa: E402
from worker.agent_client import ConsortiumClient  # noqa: E402


def review_pending(client: ConsortiumClient, goal: str = "", log=print) -> int:
    """Review every submitted task once. Returns how many were reviewed."""
    reviewed = 0
    for task in client._call("GET", "/api/tasks?state=submitted").get("tasks", []):
        verdict = orchestrator.review(task, task.get("result", ""), goal)
        body = {"decision": verdict["decision"], "note": verdict["note"]}
        if verdict.get("followup_instruction"):
            body["followup_instruction"] = verdict["followup_instruction"]
        try:
            client._call("POST", f"/api/tasks/{task['id']}/review", body)
        except Exception as e:  # e.g. leader reviewed it by hand meanwhile
            log(f"review of {task['id']} not applied: {e}")
            continue
        reviewed += 1
        log(f"{verdict['decision']:>8} {task['id']} {task.get('title', '')}: {verdict['note']}")
    return reviewed


def all_accepted(tasks: list[dict]) -> bool:
    """Done when nothing is in flight and every non-rejected task is accepted.
    (Rejected tasks are superseded by their follow-ups.)"""
    return bool(tasks) and all(t["state"] in (orchestrator.ACCEPTED, orchestrator.REJECTED)
                               for t in tasks) and \
        any(t["state"] == orchestrator.ACCEPTED for t in tasks)


def run(client: ConsortiumClient, poll: float = 2.0, auto_done: bool = False, log=print,
        stop_event: Optional[threading.Event] = None) -> None:
    stop_event = stop_event or threading.Event()
    lead = lead_ai.get_lead()
    log(f"lead agent up; reviewer = {lead.label if lead.available else 'auto-accept (no Lead AI)'}")
    while not stop_event.is_set():
        try:
            proj = (client._call("GET", "/api/project").get("project") or {})
            state = proj.get("state")
            if state == "done":
                log("project is done; lead agent exiting")
                return
            if state == "active":
                review_pending(client, proj.get("goal", ""), log)
                if auto_done and all_accepted(client.tasks()):
                    client._call("POST", "/api/project/state", {"state": "done"})
                    log("all tasks accepted; marked project done")
                    return
        except Exception as e:
            log(f"lead agent: {e}")
        stop_event.wait(poll)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Lead AI review loop for a Consortium server.")
    ap.add_argument("--server", default="http://127.0.0.1:8000")
    ap.add_argument("--provider", default=None, choices=lead_ai.PROVIDERS,
                    help="Lead AI provider (default: LEAD_PROVIDER env or first key found)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--poll", type=float, default=2.0)
    ap.add_argument("--auto-done", action="store_true",
                    help="mark the project done when every task is accepted")
    args = ap.parse_args(argv)
    if args.provider or args.model:
        lead_ai.configure(args.provider, None, args.model)

    def log(msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    try:
        run(ConsortiumClient(args.server), args.poll, args.auto_done, log)
    except KeyboardInterrupt:
        log("stopped")


if __name__ == "__main__":
    main()
