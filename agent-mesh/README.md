# Consortium — leader-driven multi-AI collaboration

A **leader** runs this server on their laptop and attaches an AI (the **Lead AI**:
Claude, Codex, or an open model like Kimi). Teammates open the LAN URL, register
the AI *they* brought (a **Worker AI**), and each worker loops: p
python -m server.app
```

On startup the server prints the exact URLs teammates need, including the
auto-detected LAN IP and a reachability link, e.g.:

```
  Leader (this machine):  http://localhost:8000
  Teammates on the LAN:   http://192.168.1.20:8000
  Reachability check:     http://192.168.1.20:8000/api/health
```

The leader opens **http://localhost:8000**.

### Step 2 — Open the firewall for the port (the #1 gotcha)

On the leader machine, in an **Administrator** PowerShell:

```powershell
New-NetFirewallRule -DisplayName "Consortium 8000" `
  -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

Then from a worker machine, confirm you can reach the leader **before** joining:

```powershell
Test-NetConnection 192.168.1.20 -Port 8000      # TcpTestSucceeded : True
# or hit the health endpoint directly:
curl http://192.168.1.20:8000/api/health         # {"ok": true, ...}
```

If `TcpTestSucceeded` is `True` / health returns `ok`, everything else works.

### Step 3 — Workers: join

**Option A — browser (simplest):** open `http://192.168.1.20:8000`, fill in the
"Bring your AI" panel (name, provider, optional key), click **Join the mesh**.
Your node appears orbiting the Lead core, and that tab starts pulling tasks.
> A key typed in the browser is sent to the leader's server over the LAN.

**Option B — Python client (key never leaves your laptop):**

```powershell
cd agent-mesh
python worker/agent_client.py --server http://192.168.1.20:8000 --owner Bob --provider mock
# real AI:  --provider anthropic   (reads ANTHROPIC_API_KEY locally, sends only results)
```

### Step 4 — Leader: run the mission

In the UI: type a goal → **Plan & launch** → watch Bob's and Carol's AIs claim
and submit tasks in parallel on the constellation → review submitted tasks
(**Accept** / **Reject** with a follow-up) → **Mark done**.

### Simulate on one machine

Each worker is independent, so three terminals reproduce the 3-computer run:

```powershell
# terminal 1 — leader
cd agent-mesh; python -m server.app
# terminal 2 — Bob
cd agent-mesh; python worker/agent_client.py --server http://127.0.0.1:8000 --owner Bob --provider mock
# terminal 3 — Carol
cd agent-mesh; python worker/agent_client.py --server http://127.0.0.1:8000 --owner Carol --provider mock
```

Open `http://localhost:8000` as the leader and plan a goal — both workers pull
concurrently, exactly as they would across the LAN.

### Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Worker can't load the page | Firewall (Step 2); or different network / guest-wifi isolation. Use `Test-NetConnection`. |
| Page loads, nothing updates live | A VPN/proxy is buffering the `/api/events` SSE stream. Test on a plain LAN. |
| `localhost` doesn't work on a worker | Use the leader's **LAN IP**, not `localhost` (that points at the worker itself). |
| A worker laptop drops off | Self-heals: a silent worker's in-flight task is auto-requeued for another worker. |

## Share publicly with Cloudflare Tunnel (no firewall, no VPN)

If the other computers aren't on your network — or you'd rather not open the
Windows Firewall — expose the server through a Cloudflare "quick tunnel". It
gives you a public `https://<random>.trycloudflare.com` URL that teammates open
from anywhere. One command does everything:

```powershell
cd agent-mesh
powershell -ExecutionPolicy Bypass -File .\share.ps1
```

`share.ps1` will:
1. download `cloudflared` once (to `~\.consortium\bin\`, no admin needed),
2. start the Consortium server and wait until `/api/health` is green,
3. open the tunnel and print your public URL.

Hand the printed `https://…trycloudflare.com` URL to your teammates; they open
it in a browser and click **Join the mesh**. Press **Ctrl+C** in that window to
stop both the tunnel and the server.

Options:

```powershell
# require a room code so only people with it can register a worker:
powershell -ExecutionPolicy Bypass -File .\share.ps1 -RoomCode hunter2

# use a real Lead AI (needs the key in .env or env):
powershell -ExecutionPolicy Bypass -File .\share.ps1 -LeadProvider anthropic
```

> The `trycloudflare.com` URL is **public** while the tunnel runs and **changes**
> each restart, so start it once and keep the window open. Use `-RoomCode` for a
> shared demo, and Ctrl+C when you're done so it doesn't linger.

## API

The full, frozen HTTP + SSE contract is **`WORK_SPLIT.md` §4**. The frontend is
built against that section alone. Quick smoke check:

```bash
curl http://localhost:8000/api/health                 # liveness + reachability probe
curl http://localhost:8000/api/project
curl -X POST http://localhost:8000/api/project  -d '{"goal":"demo landing page"}'
curl -X POST http://localhost:8000/api/project/plan -d '{}'
curl http://localhost:8000/api/tasks
```

`GET /api/health` returns `{ok, service, uptime, project_state, workers_online,
sse_clients}` — teammates (or a monitor) hit it to confirm the server is
reachable before joining.

The server serves the frontend from `server/webui/` (a live constellation
"mission control" UI — pure canvas + vanilla JS, no build step). If that folder
is absent it falls back to a minimal status page.

## Tests (no keys, no network)

```bash
cd agent-mesh
python -m tests.test_store          # core store: atomic claim, deps, rejection rewire, stale requeue
python -m tests.test_server_routes  # every §4 route + SSE event shapes
python -m tests.test_orchestrator   # Agent B: planning/review/summarize
python -m tests.test_e2e            # full A+B integration: plan → workers → done
python -m tests.test_webui_serving  # static assets + /api/health + SSE snapshot shape
```

## File ownership (see `WORK_SPLIT.md`)

| Area | Owner | Files |
|---|---|---|
| State, store, lifecycle | Agent A | `core/*` |
| HTTP server, routing, SSE | Agent A | `server/*`, `README.md` |
| Lead AI + orchestration | Agent B | `lead/*` |
| Worker AI + client loop | Agent B | `worker/*` |
| Integration test | Both | `tests/test_e2e.py` |
ull the next
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
python -m server.app                 # http://localhost:8000 (binds 0.0.0.0 for LAN)
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

## Testing across 3 computers (full LAN demo)

The real demo: one leader + two teammates, each on their own machine, same
network. (No 3 machines handy? Jump to *Simulate on one machine* below — it
proves identical behaviour.)

Roles:

- **Computer 1 — Leader:** runs the server and opens the control UI.
- **Computer 2 — Worker (Bob)** and **Computer 3 — Worker (Carol):** open the
  website the leader hosts and bring their own AI.

### Step 1 — Leader: start the server

```powershell
cd agent-mesh
$env:LEAD_PROVIDER="mock"        # or anthropic / openai / kimi