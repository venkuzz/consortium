# Consortium — Leader / Worker Build Plan

This document is the single source of truth for building **Consortium**: a
leader-driven, multi-AI collaboration system. It splits the work between **two
backend AI agents** (Agent A and Agent B) pushing to the **same GitHub repo**,
and specifies **every HTTP endpoint** in enough detail that a **third AI agent**
can build the frontend without reading the backend source.

---

## 1. Product in one paragraph

A **leader** runs a local server on their machine and attaches an AI (the
**Lead AI** — e.g. Claude Code, Codex, or an open model like Kimi). The leader
shares the LAN URL. **Teammates** open that website, register the AI they
brought (the **Worker AI**). Each worker then runs a loop: it **pulls the next
instruction** generated/approved by the Lead AI, executes it with its own AI,
and **submits the result** back. The Lead AI reviews results and issues the next
instruction. This continues until the **leader marks the project done**.

```
                       ┌──────────────────────────────┐
   Leader's machine →  │   Consortium Server (host)    │
                       │  ┌────────────┐  ┌─────────┐  │
   Lead AI  ◄────────► │  │ Orchestrat-│  │  Task   │  │
  (Claude/Codex/Kimi)  │  │ or (Lead)  │  │  Queue  │  │
                       │  └────────────┘  └─────────┘  │
                       │        ▲   HTTP + SSE   ▲      │
                       └────────┼────────────────┼─────┘
                                │                │
          Teammate browser  ────┘         ┌──────┴───────┐
          (frontend UI)                    │ Worker AIs   │
                                           │ (poll loop)  │
                                           └──────────────┘
```

**Pull model, not push.** Workers *poll* (short poll or long-poll) for the next
task. This is deliberately resilient to flaky hackathon wifi and needs no
websockets. The browser UI uses **SSE** for live read-only updates.

---

## 2. Repository layout (shared repo: `venkuzz/consortium`)

All new code lives under `consortium-leader-worker/`. Each agent owns disjoint
files to minimize merge conflicts.

```
consortium-leader-worker/
├─ WORK_SPLIT.md            # this file (shared, edit via PR only)
├─ README.md                # Agent A owns
├─ requirements.txt         # stdlib only; keep empty of deps
│
├─ core/                    # ── AGENT A owns ──
│  ├─ state.py              # Project, Worker, Task dataclasses + in-memory store
│  ├─ store.py              # thread-safe repository (CRUD + queue ops)
│  └─ lifecycle.py          # project state machine (planning→active→done)
│
├─ lead/                    # ── AGENT B owns ──
│  ├─ lead_ai.py            # Lead AI wrapper (anthropic/openai/kimi/mock)
│  ├─ orchestrator.py       # turns leader goal → task list, reviews results
│  └─ prompts.py            # system prompts for planning + review
│
├─ worker/                  # ── AGENT B owns ──
│  ├─ worker_agent.py       # uniform Worker AI wrapper (reuse mesh pattern)
│  └─ agent_client.py       # reference worker poll-loop client (python)
│
├─ server/                  # ── AGENT A owns ──
│  ├─ app.py                # HTTP server, routing, SSE hub, entrypoint
│  ├─ routes_project.py     # /api/project/* handlers
│  ├─ routes_worker.py      # /api/worker/* handlers
│  └─ routes_stream.py      # /api/events SSE handler
│
└─ tests/
   ├─ test_store.py         # Agent A
   ├─ test_orchestrator.py  # Agent B
   └─ test_e2e.py           # SHARED — integration, written last, pair on it
```

### Ownership summary

| Area | Owner | Files |
|---|---|---|
| State, store, lifecycle | **Agent A** | `core/*` |
| HTTP server, routing, SSE | **Agent A** | `server/*`, `requirements.txt`, `README.md` |
| Lead AI + orchestration | **Agent B** | `lead/*` |
| Worker AI + client loop | **Agent B** | `worker/*` |
| Integration test | **Both** | `tests/test_e2e.py` (coordinate) |

**Rule:** no agent edits another agent's files. If you need a change in the
other agent's area, open an issue / note in the PR and let the owner make it.
The only shared, cross-cutting contracts are the **dataclasses in `core/state.py`**
and the **API schema in §4** — both are frozen below so you can build in parallel.

---

## 3. Frozen data model (`core/state.py`)

Agent A implements these exactly; Agent B and the frontend code against them.
Do not rename fields without a PR that updates this section.

```python
# Project lifecycle states
PLANNING = "planning"   # leader is defining the goal; no tasks dispatched yet
ACTIVE   = "active"     # tasks are being pulled/executed
PAUSED   = "paused"     # leader halted dispatch; workers idle
DONE     = "done"       # leader declared completion; workers stop

# Task lifecycle states
QUEUED      = "queued"       # created, not yet claimed
ASSIGNED    = "assigned"     # claimed by a worker, in progress
SUBMITTED   = "submitted"    # worker returned a result, awaiting lead review
ACCEPTED    = "accepted"     # lead accepted the result
REJECTED    = "rejected"     # lead rejected; may spawn a follow-up task
```

```python
@dataclass
class Project:
    id: str
    goal: str                 # leader's high-level objective
    state: str                 # one of PLANNING/ACTIVE/PAUSED/DONE
    created_at: float
    updated_at: float
    summary: str = ""          # lead AI's rolling summary of progress

@dataclass
class Worker:
    id: str                    # "alice-claude-1a2b"
    owner: str                 # "Alice"
    provider: str              # "anthropic"|"openai"|"kimi"|"mock"
    display: str               # "Alice's Claude"
    status: str                # "idle"|"working"|"offline"
    last_seen: float           # epoch; used to detect dropouts
    current_task_id: str | None = None

@dataclass
class Task:
    id: str
    project_id: str
    seq: int                   # monotonic ordering for the UI
    title: str                 # short label
    instruction: str           # full instruction text from the Lead AI
    state: str                 # QUEUED/ASSIGNED/SUBMITTED/ACCEPTED/REJECTED
    assigned_to: str | None    # worker id
    result: str = ""           # worker's submitted output
    review_note: str = ""      # lead's note on accept/reject
    depends_on: list[str] = field(default_factory=list)  # task ids
    created_at: float = 0.0
    updated_at: float = 0.0
```

---

## 4. HTTP API CONTRACT (frozen — frontend agent codes against THIS only)

- Base URL: `http://<leader-lan-ip>:8000`
- All request/response bodies are **JSON** unless noted. All responses include
  CORS header `Access-Control-Allow-Origin: *`.
- Errors: non-2xx with body `{"error": "<human message>"}`.
- Timestamps are epoch seconds (float).
- There is **one active project at a time** (single room; fine for a hackathon).

### 4.1 Roles

Two client roles hit the API:
- **Leader UI** (the host's browser) — manages the project, drives the Lead AI.
- **Worker** (teammate's browser tab OR the python `agent_client.py`) — registers,
  then loops: claim → execute → submit.

No auth for the MVP. A single optional shared `room_code` MAY be posted on
register; if the host set one, mismatches return `403`. Keep it optional.

---

### 4.2 Project endpoints  (owner: Agent A, logic calls into Agent B)

#### `GET /api/project`
Return the current project (or `{"project": null}` if none).
```json
{ "project": {
    "id": "p_8f3a", "goal": "Build a landing page for X",
    "state": "active", "summary": "2/5 tasks accepted...",
    "created_at": 1730000000.0, "updated_at": 1730000500.0 },
  "stats": { "queued": 2, "assigned": 1, "submitted": 0,
             "accepted": 2, "rejected": 0, "workers_online": 3 } }
```

#### `POST /api/project`
Create (or reset) the project. Leader only.
Request: `{ "goal": "Build a landing page for a team-matching app" }`
Response: `{ "project": { ...as above, "state": "planning" } }`
Side effect: clears any previous tasks.

#### `POST /api/project/plan`
Ask the **Lead AI** to turn the goal into an initial task list, then move the
project to `active`. Leader only. May take several seconds.
Request: `{}` (uses stored goal) or `{ "goal": "override goal" }`
Response:
```json
{ "project": { ...state:"active" },
  "tasks": [ { "id":"t_01","seq":1,"title":"...","instruction":"...",
               "state":"queued","depends_on":[] }, ... ],
  "used_llm": true,
  "note": "Lead AI generated 5 tasks (fell back to template if false)." }
```
> Implementation note for Agent B: this calls `orchestrator.plan(goal)`. If the
> Lead AI is unavailable, fall back to a deterministic template split so the demo
> never dies (mirror the mesh's graceful-degradation philosophy).

#### `POST /api/project/state`
Leader changes lifecycle state directly.
Request: `{ "state": "paused" }`  (allowed: `active`, `paused`, `done`)
Response: `{ "project": { ...updated } }`
Rules enforced server-side (Agent A, `lifecycle.py`):
- `planning → active` only via `/plan` (or explicit if tasks already exist).
- any → `paused` → `active` allowed.
- any → `done` allowed (terminal; workers must stop pulling).
- `done` is terminal; to restart, `POST /api/project` again.

#### `GET /api/tasks`
List all tasks for the current project, ordered by `seq`.
Optional query: `?state=queued` to filter.
```json
{ "tasks": [ { "id":"t_01","seq":1,"title":"Hero section",
   "instruction":"Build the hero markup with ids #hero, #cta ...",
   "state":"accepted","assigned_to":"alice-claude-1a2b",
   "result":"<header>...","review_note":"LGTM","depends_on":[],
   "created_at":...,"updated_at":... }, ... ] }
```

#### `POST /api/tasks`
Leader manually adds a task (bypasses the Lead AI). Appends to the queue.
Request: `{ "title": "Add footer", "instruction": "Build a footer with ...",
            "depends_on": ["t_01"] }`
Response: `{ "task": { ...created, "state":"queued" } }`

#### `POST /api/tasks/{id}/review`
Leader (or Lead AI on the leader's behalf) accepts or rejects a **submitted**
task. On reject, optionally auto-enqueue a follow-up.
Request: `{ "decision": "accepted", "note": "looks good" }`
   or     `{ "decision": "rejected", "note": "missing form",
             "followup_instruction": "Redo with a signup form" }`
Response: `{ "task": { ...updated }, "followup": { ...new task }|null }`

---

### 4.3 Worker endpoints  (owner: Agent A routing; Agent B supplies worker logic)

#### `POST /api/worker/register`
A teammate registers the AI they brought. Returns a worker id used in all
later calls.
Request:
```json
{ "owner": "Alice", "provider": "anthropic",
  "api_key": "optional — uses host env if blank",
  "model": "optional", "room_code": "optional" }
```
Response:
```json
{ "worker": { "id":"alice-claude-1a2b","display":"Alice's Claude",
              "provider":"anthropic","status":"idle" },
  "project_state": "active" }
```
`provider` ∈ `anthropic | openai | kimi | mock`.

#### `POST /api/worker/{id}/heartbeat`
Keep-alive; updates `last_seen`. Workers call every ~5s.
Response: `{ "ok": true, "project_state": "active" }`
If `project_state` is `done` or `paused`, the worker should stop/suspend pulling.

#### `POST /api/worker/{id}/next`
**The core pull.** Atomically claim the next eligible `queued` task (whose
`depends_on` are all `accepted`) and mark it `assigned` to this worker.
- If a task is available: return it and set worker `status="working"`.
- If none available but project active: return `{"task": null, "wait": true}`
  (worker should poll again after a short delay / may long-poll — see §4.4).
- If project `done`/`paused`: return `{"task": null, "stop": true}`.
Response (task available):
```json
{ "task": { "id":"t_03","seq":3,"title":"Signup form",
            "instruction":"Build a signup form with ids #email #join ...",
            "depends_on":["t_01"] },
  "project": { "id":"p_8f3a","goal":"...","summary":"..." } }
```
> Atomicity is on Agent A (`store.claim_next(worker_id)` under a lock).

#### `POST /api/worker/{id}/submit`
Worker returns the result for its current assigned task → state `submitted`.
Request: `{ "task_id": "t_03", "output": "<header>...</header>",
            "ok": true, "error": "", "elapsed": 4.2 }`
Response: `{ "ok": true, "task_state": "submitted" }`
Side effect: worker `status="idle"`, `current_task_id=null`. Emits SSE
`task_submitted`.

#### `GET /api/workers`
Roster for the UI.
```json
{ "workers": [ { "id":"...","display":"Alice's Claude","provider":"anthropic",
    "status":"working","current_task_id":"t_03","last_seen":1730000500.0 } ] }
```

#### `POST /api/worker/{id}/unregister`
Remove a worker; requeue its in-flight task.
Response: `{ "ok": true }`

---

### 4.4 Live updates  (owner: Agent A, `routes_stream.py`)

#### `GET /api/events`  (Server-Sent Events)
A read-only event stream for the UI. `Content-Type: text/event-stream`.
The server pushes these event types (each `data:` is a JSON object):

| event | payload | meaning |
|---|---|---|
| `snapshot` | `{project, tasks, workers, stats}` | full state, sent once on connect |
| `project_state` | `{state, summary}` | lifecycle changed |
| `tasks_planned` | `{tasks:[...]}` | Lead AI produced a task list |
| `task_created` | `{task}` | a task was added |
| `task_claimed` | `{task_id, worker_id}` | worker pulled a task |
| `task_submitted` | `{task_id, worker_id, ok}` | result returned |
| `task_reviewed` | `{task_id, decision, note, followup_id?}` | lead reviewed |
| `worker_joined` | `{worker}` | new worker |
| `worker_left` | `{worker_id}` | worker gone/offline |
| `done` | `{summary}` | leader ended the project |

**Frontend contract:** on connect, consume `snapshot`, render everything, then
apply subsequent events incrementally. Reconnect on drop and re-consume
`snapshot`. No other polling is required for the UI.

> Optional long-poll alternative for `/next`: if a client sends
> `?wait=25`, Agent A may hold the request up to 25s until a task is available
> before returning `{"task": null, "wait": true}`. Short-poll must always work;
> long-poll is an enhancement.

---

### 4.5 Worker loop (reference pseudocode — Agent B ships this in `agent_client.py`)

```
register() -> worker_id
loop:
    hb = heartbeat(worker_id)
    if hb.project_state in ("done","paused"): sleep(2); continue/stop
    r = POST /api/worker/{id}/next
    if r.stop: break
    if r.task is null: sleep(2); continue          # or long-poll
    out = run_worker_ai(my_provider, r.task.instruction, r.project)
    POST /api/worker/{id}/submit {task_id, output, ok, elapsed}
```

---

## 5. Build phases & task breakdown

### Phase 0 — scaffolding (both, ~15 min, do first, single PR each)
- **Agent A:** create `core/`, `server/`, `requirements.txt`, stub each module
  with the signatures below. Commit on branch `feat/core-skeleton`.
- **Agent B:** create `lead/`, `worker/`, stub modules + prompts. Commit on
  branch `feat/lead-skeleton`.
- Both branches only add new files → no conflicts. Merge both to `main`.

### Phase 1 — Agent A (core + server)
1. `core/state.py` — dataclasses from §3 (frozen). **Blocks everyone, do first.**
2. `core/store.py` — thread-safe store:
   `create_project`, `get_project`, `set_state`, `add_task`, `list_tasks`,
   `claim_next(worker_id)` (atomic), `submit(task_id, result)`,
   `review(task_id, decision, note)`, `register_worker`, `heartbeat`,
   `list_workers`, `remove_worker`, `stats()`.
3. `core/lifecycle.py` — validate state transitions (§4.2 rules).
4. `server/app.py` — stdlib `ThreadingHTTPServer`, routing table, SSE hub
   (`broadcast(event, data)` fan-out to connected clients), serves the frontend
   build from `server/static/` if present.
5. `server/routes_project.py`, `routes_worker.py`, `routes_stream.py` — wire
   every endpoint in §4 to the store; call `orchestrator`/`lead_ai` (Agent B)
   for `/plan` and review-assist via a small interface (see §6).
6. `tests/test_store.py` — claim atomicity, dependency gating, transitions.

### Phase 2 — Agent B (lead + worker)
1. `lead/prompts.py` — planning prompt (goal → JSON task list) and review prompt
   (task + result → accept/reject + note). Strict-JSON out.
2. `lead/lead_ai.py` — `LeadAI` wrapper over `anthropic|openai|kimi|mock`
   (reuse the `agent-mesh/agents.py` uniform-wrapper pattern; add a `kimi`
   branch — OpenAI-compatible endpoint, base URL via `KIMI_BASE_URL`).
3. `lead/orchestrator.py`:
   - `plan(goal) -> list[TaskSpec]` (LLM, with deterministic fallback).
   - `review(task, result) -> {decision, note, followup?}` (LLM, fallback: accept).
   - `summarize(project, tasks) -> str` rolling summary for the UI.
4. `worker/worker_agent.py` — uniform Worker AI wrapper (same providers).
5. `worker/agent_client.py` — the poll-loop client from §4.5; runnable as
   `python agent_client.py --server http://host:8000 --owner Alice --provider mock`.
6. `tests/test_orchestrator.py` — planning JSON parsing + fallback, review paths.

### Phase 3 — integration (both, pair)
- `tests/test_e2e.py`: create project → plan → register 2 mock workers →
  run their loops in threads → all tasks reach `accepted` → mark `done`.
  Assert SSE events fired in the right order. Mirror `agent-mesh/selftest.py`.
- Agent A ensures the server serves the frontend agent's built assets.

---

## 6. The one interface between Agent A and Agent B (keep it tiny)

So the server (A) can call the brain (B) without tight coupling, B exposes a
module-level facade the server imports. **This is the only import across the A/B
boundary.**

```python
# lead/orchestrator.py  (Agent B provides)
def plan(goal: str) -> dict:
    """-> {'tasks': [{'title','instruction','depends_on'}], 'used_llm': bool, 'note': str}"""

def review(task: dict, result: str) -> dict:
    """-> {'decision': 'accepted'|'rejected', 'note': str, 'followup_instruction': str|None}"""

def summarize(project: dict, tasks: list[dict]) -> str:
    """-> short progress summary string"""
```

Agent A calls only these three functions. If `lead/` isn't merged yet, A stubs
them to deterministic output so the server runs standalone.

---

## 7. Git workflow (shared repo, 2 agents, avoid conflicts)

- Branch per unit of work: `feat/<area>-<short>`, e.g. `feat/core-store`,
  `feat/lead-orchestrator`. Never commit straight to `main`.
- One PR per branch. Keep PRs small and within your owned files.
- **File ownership is the conflict-avoidance strategy** — agents touch disjoint
  files, so merges are clean. The only shared files are `WORK_SPLIT.md` (this,
  edit via PR) and `tests/test_e2e.py` (Phase 3, coordinate turns).
- Pull/rebase `main` before opening a PR. Resolve by re-reading §3 and §4 as the
  source of truth — the contract wins over any local drift.
- Commit messages: `area: what changed` (e.g. `store: atomic claim_next under lock`).
- Merge order: `core/state.py` first (unblocks all), then everything else in
  parallel, then `test_e2e.py` last.

---

## 8. Definition of done (per agent)

**Agent A done when:** server boots with `python server/app.py`, every §4
endpoint responds per contract (verified with `curl`), `test_store.py` passes,
SSE `snapshot` + incremental events work, and a mock worker can register/claim/
submit end-to-end even with `lead/` stubbed.

**Agent B done when:** `orchestrator.plan`/`review`/`summarize` return valid
shapes with real LLMs and fall back cleanly without keys, `kimi` provider works
against an OpenAI-compatible endpoint, `agent_client.py` runs a full loop against
a live server, and `test_orchestrator.py` passes.

**Frontend (3rd agent) done when:** using ONLY §4, the UI lets a leader set a
goal, click Plan, watch tasks + workers update live over `/api/events`, review
submitted tasks, and click Done — and lets a teammate register a worker and see
their AI pull/submit tasks. No backend source reading required.

---

## 9. Non-goals for the MVP (don't build these)
- Persistence across restarts (in-memory is fine).
- Multiple concurrent projects / multi-room.
- Real authentication (optional `room_code` only).
- Websockets (SSE + polling is deliberate).
- Zero third-party deps — keep it stdlib-only like `agent-mesh`.
```
