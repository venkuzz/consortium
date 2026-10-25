# Consortium — leader-driven multi-AI collaboration

A **leader** runs this server on their laptop and attaches an AI (the **Lead AI**:
Claude, Codex, or an open model like Kimi). Teammates open the LAN URL, register
the AI *they* brought (a **Worker AI**), and each worker loops: pull the next
instruction from the Lead AI, run it, submit the result. The Lead AI reviews
results and issues follow-ups. It runs until the **leader marks the project done**.

> This directory also contains the original one-shot **Agent Mesh** demo
> (`server.py`, `agents.py`, `dispatcher.py`). Consortium is the newer
> leader/worker system and lives in `core/`, `server/`, `lead/`, `worker/`.

## Architecture

```
leader's machine
  ┌───────────────────────────────────────┐
  │  server/app.py  (stdlib HTTP + SSE)     │
  │    ├─ core/      state, store, lifecycle│  ← Agent A
  │    ├─ lead/      Lead AI + orchestrator │  ← Agent B
  │    └─ worker/    worker AI + poll client│  ← Agent B
  └───────────────────────────────────────┘
        ▲ HTTP+SSE            ▲ HTTP (poll)
   teammate browsers     teammate worker loops
```

Design choices: **pull model** (workers poll `/next`; resilient to flaky wifi),
**SSE** for live UI updates, **zero third-party dependencies** (Python 3.10+
stdlib only), and **graceful degradation** (no Lead AI key → deterministic
template plan + auto-accept, so the demo always runs).

## Run it (leader)

```bash
cd agent-mesh
python -m server.app                 # http://0.0.0.0:8000
PORT=9000 python -m server.app       # custom port
```

A `.env` in `agent-mesh/` is auto-loaded (no library needed):

```
LEAD_PROVIDER=anthropic              # or openai | kimi | mock
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
KIMI_API_KEY=...
KIMI_BASE_URL=https://...            # OpenAI-compatible endpoint for Kimi
CONSORTIUM_ROOM_CODE=hunter2         # optional: require a room code to join
```

Teammates then open `http://<leader-LAN-ip>:8000`. Find the IP with `ipconfig`
(Windows) or `ifconfig`/`ip addr` (macOS/Linux).

## Typical flow

1. Leader: `POST /api/project {goal}` then `POST /api/project/plan` — the Lead AI
   turns the goal into tasks and the project goes `active`.
2. Teammates: register a worker (browser UI or the python client below).
3. Workers loop `heartbeat → next → submit`; the Lead AI reviews each result.
4. Leader watches progress live over `/api/events` and finally
   `POST /api/project/state {"state":"done"}`.

### Python worker client (teammate, no browser needed)

```bash
cd agent-mesh
python worker/agent_client.py --server http://<leader-ip>:8000 --owner Alice --provider mock
# real AI:  --provider anthropic   (key stays on the teammate's machine)
```

### Lead auto-reviewer (optional, runs on the leader's side)

Without review, dependent tasks (e.g. the final integration task) never unblock.
The Lead AI can review automatically over the public API:

```bash
python -m lead.lead_agent --server http://127.0.0.1:8000 --auto-done
```

## API

The full, frozen HTTP + SSE contract is **`WORK_SPLIT.md` §4**. The frontend is
built against that section alone. Quick smoke check:

```bash
curl http://localhost:8000/api/project
curl -X POST http://localhost:8000/api/project  -d '{"goal":"demo landing page"}'
curl -X POST http://localhost:8000/api/project/plan -d '{}'
curl http://localhost:8000/api/tasks
```

The server serves a built frontend from `server/static/` if present, otherwise a
minimal status page. The frontend agent drops its build into `server/static/`.

## Tests (no keys, no network)

```bash
cd agent-mesh
python -m tests.test_store          # core store: atomic claim, deps, rejection rewire, stale requeue
python -m tests.test_server_routes  # every §4 route + SSE event shapes
python -m tests.test_orchestrator   # Agent B: planning/review/summarize
python -m tests.test_e2e            # full A+B integration: plan → workers → done
```

## File ownership (see `WORK_SPLIT.md`)

| Area | Owner | Files |
|---|---|---|
| State, store, lifecycle | Agent A | `core/*` |
| HTTP server, routing, SSE | Agent A | `server/*`, `README.md` |
| Lead AI + orchestration | Agent B | `lead/*` |
| Worker AI + client loop | Agent B | `worker/*` |
| Integration test | Both | `tests/test_e2e.py` |
