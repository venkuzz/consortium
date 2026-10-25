# Agent B → Agent A handoff

Everything in WORK_SPLIT.md §6 is implemented in `lead/orchestrator.py`. The
signatures are unchanged, with one optional kwarg added: `review(task, result, goal="")`.

## Importing

The modules use package imports (`from lead import ...`), so `agent-mesh/` has to be
on `sys.path`. If you run `python server/app.py`, add this before importing:

```python
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from lead import orchestrator, lead_ai
```

## Two changes needed in `core/store.py`

1. **`plan()` depends_on values are 0-based indexes**, not task ids, because no ids
   exist yet when the plan is made. `add_tasks_bulk` currently copies them through
   as-is. Map them inside the loop, for example:
   ```python
   deps = [created[d].id if isinstance(d, int) and 0 <= d < len(created) else d
           for d in spec.get("depends_on") or []]
   ```
2. **A rejected task blocks its dependents forever.** `_deps_satisfied` requires
   ACCEPTED, but after a rejection the work is redone in a new follow-up task. One
   fix: when a follow-up is created, replace the rejected id with the follow-up id
   in the `depends_on` of every queued task.

## Other useful hooks

- `lead_ai.configure(provider, api_key, model)` swaps the Lead AI at runtime, e.g. when
  the leader picks it in the UI. By default it reads `LEAD_PROVIDER`, or uses the first
  provider with a key in env, or mock. Mock or no key means template plan + auto-accept.
- `orchestrator.summarize(project, tasks)` takes plain dicts (`Task.to_dict()`), is
  deterministic and cheap, and is safe to call after every submit or review.
  `use_llm=True` asks the Lead AI to write it instead.
- `worker.worker_agent.run_worker(provider, task, project, dependency_outputs,
  api_key, model)` returns the exact `/submit` body except `task_id`. Use it if the
  server ever runs AI calls for browser-registered workers.
- Python workers: `python worker/agent_client.py --server http://host:8000 --owner Alice --provider mock`.
  The worker's API key never leaves their laptop. The client sends `POST /api/worker/{id}/next`
  with an empty JSON body, and adds `?wait=N` only when `--long-poll` is set.
