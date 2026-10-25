# Consortium frontend

Ownership: frontend only. This directory is the only area changed by this branch.
Built against `agent-mesh/WORK_SPLIT.md` §4, not the older mesh endpoints.

## Server integration

`server/app.py` already serves this directory at `/`, with `app.css` and
`app.js` alongside the document. All API requests are same-origin. The
frontend consumes `/api/events` snapshots and named events; on incremental
events it refreshes the authoritative project, task, and worker endpoints. No
backend files are changed by this branch.

The work plan names `consortium-leader-worker/`, while the current repository
uses `agent-mesh/`. These assets live at `agent-mesh/server/webui/`, the
directory configured by `server/app.py`.

## Preview without the backend

From this directory run `python3 -m http.server 8000`, then open `http://localhost:8000/?demo=1`.
The explicit demo uses local sample state only. It never calls the API or stores keys. Select Plan tasks, then Run sample workers, inspect/review their output, and mark the project done. Reload resets it.

Without `?demo=1`, the UI connects to the real API and never substitutes fake data when a server fails. Use the backend server, not the static preview server, for live mode.

## Supported flows

- Leader: create/reset goal, request Lead AI plan, add tasks with dependencies, pause/resume/done, accept/reject submitted work with notes and optional follow-up.
- Teammate: register Claude/OpenAI/Kimi/mock worker; heartbeat while this tab is open; claim a task, copy its instruction, execute with their own AI, paste and submit output. Registration alone does not execute a model. Agent B's Python client supplies automatic execution.
- Live roster, task filters, task detail with instruction/result/review, dependency labels, project summary and activity. Generated output is text only; HTML preview runs inside an opaque-origin sandbox.
- Worker ownership in this browser is held in sessionStorage (IDs only); API keys are cleared after submission and never persisted by the frontend.

## Design direction

A working table for a technical team. The task queue is the focal point, with a blue lead marker, readable task rows, and a persistent detail inspector. No marketing hero or fabricated metrics.
Palette: paper #f3f6f9, white #ffffff, ink #20334c, ocean #2359b6, steel #64758b, sage #267b65. Typography: Avenir Next/Trebuchet for human-readable workspace text; monospace only for source output. Left-aligned content. Compact nav, wider task list, narrower inspector; collapse to a single column on mobile. The roster and progress refer only to real state (or the explicitly labeled demo).

Review against the brief: the earlier violet page-builder UI targeted the old two-slot API. This version removes that invented constraint, uses the leader/worker API, and gives task states and review actions priority over decorative empty states.
