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

## Connect your CLI agent to the leader (from another computer)

Your CLI agent and the leader don't connect directly — both join the **same
leader URL**, and the leader's AI routes work between everyone. A tiny relay
(`agent_client.py`) on your machine pulls a task, pipes it to your CLI agent's
**stdin**, and sends its **stdout** back. Your CLI tool keeps using its own
login; no API key goes to the leader.

### ⚠️ Run from the `agent-mesh` folder

The `worker/agent_client.py` path is **relative to `agent-mesh/`**. If you see:

```
can't open file '...\Consortium\worker\agent_client.py': [Errno 2] No such file or directory
```

you're one directory too high. Fix it by `cd`-ing into `agent-mesh` first:

```powershell
# WRONG — missing the agent-mesh folder:
#   cd consortium ; python worker/agent_client.py ...
# RIGHT:
cd "<path-to>\consortium\agent-mesh"
python worker/agent_client.py --server https://<leader-url> --owner You --provider cli --cli-cmd "claude -p"
```

(Or run it by absolute path from anywhere:
`python "<path>\consortium\agent-mesh\worker\agent_client.py" ...` — but you
still need to be able to import the `worker`/`lead` packages, so `cd agent-mesh`
is simplest.)

### Full walkthrough: two computers, two CLIs, one leader

**Leader (computer 1)** — start the server and a public tunnel:

```powershell
cd "<path>\consortium\agent-mesh"
powershell -ExecutionPolicy Bypass -File .\share.ps1
# copy the printed https://<something>.trycloudflare.com URL and share it
```

**You (computer 2)** — your CLI agent joins:

```powershell
cd "<path>\consortium\agent-mesh"
python worker/agent_client.py --server https://<leader-url> --owner You --provider cli --cli-cmd "claude -p"
```

**Teammate (computer 3)** — their CLI agent joins the **same** URL:

```powershell
cd "<path>\consortium\agent-mesh"
python worker/agent_client.py --server https://<leader-url> --owner Them --provider cli --cli-cmd "aider --message-file -"
```

Now the leader types a goal and clicks **Plan & launch**. Both CLIs pull tasks;
when the leader accepts your output it becomes a dependency input for your
teammate's task — that's how one CLI's work reaches the other, via the leader.

### Requirements for the `--cli-cmd` command

Your agent must run in **one-shot / print mode**: read a prompt, print the
result, exit. Test it standalone first:

```powershell
echo "say hello in one word" | claude -p          # Claude Code
echo "say hello" | aider --message-file -         # aider
```

- The task instruction is piped to **stdin** automatically. If your tool takes
  the prompt as an argument instead, put `{prompt}` in the command and it'll be
  substituted: `--cli-cmd "my-agent --task {prompt}"`.
- Slow agent (big local model, etc.)? Raise the timeout before launching
  (default 300s):

  ```powershell
  $env:CONSORTIUM_CLI_TIMEOUT = "900"   # 15 minutes
  python worker/agent_client.py --server https://<leader-url> --owner You --provider cli --cli-cmd "ollama run qwen3-coder:30b"
  ```

- No API key is ever sent to the leader — your CLI tool authenticates itself.

> Want a model API instead of a CLI tool? Use `--provider anthropic|openai|kimi`
> (key stays local), or `--provider custom --base-url <openai-compatible-url>
> --model <name>` for anything else (OpenRouter, Groq, local Ollama's `/v1`, …).

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

### Standalone worker — one file, runs from any folder (no repo clone)

If a teammate doesn't have the repo (or hit `agent-mesh not found` from running in
the wrong folder), they can grab a single self-contained worker file straight
from the leader's URL and run it anywhere:

```powershell
# download the one file from the leader's server (works over the tunnel too):
curl -o consortium_worker.py https://<leader-url>/worker
# run it from wherever it landed — no cd agent-mesh, no git clone:
python consortium_worker.py --server https://<leader-url> --owner Bob --provider cli --cli-cmd "claude -p"
```

It's stdlib-only and has zero local imports, so an empty folder is fine. All the
same flags as `agent_client.py` (`--provider`, `--cli-cmd`, `--worker-id`,
`--base-url`, `--api-key`, …). The full-repo client at `worker/agent_client.py`
still works too — the standalone file is just the no-setup option.

### Python worker client (teammate, no browser needed)

```bash
cd agent-mesh
python worker/agent_client.py --server http://<leader-ip>:8000 --owner Alice --provider mock
# real AI:  --provider anthropic   (key stays on the teammate's machine)
```

### Use your own CLI agent as the worker (no API key — "most CLI")

A teammate can plug in their **existing command-line agent** — Claude Code,
Codex CLI, Gemini CLI, aider, or any script — instead of handing over a model
API key. The lead's instruction is piped to the command on **stdin** (or
substituted for `{prompt}`); the command's **stdout** becomes the result. The
CLI tool uses its own auth, so nothing secret leaves the teammate's machine.

```bash
# Claude Code in print mode:
python worker/agent_client.py --server https://<tunnel-or-ip> --owner Bob \
    --provider cli --cli-cmd "claude -p"

# aider (reads the task from stdin):
python worker/agent_client.py --server https://<tunnel> --owner Carol \
    --provider cli --cli-cmd "aider --message-file -"

# any custom script that reads a prompt and prints a deliverable:
python worker/agent_client.py --server https://<tunnel> --owner Dave \
    --provider cli --cli-cmd "./my-agent.sh {prompt}"
```

This works over the Cloudflare tunnel too — point `--server` at the
`https://…trycloudflare.com` URL and the CLI agent participates from anywhere,
no inbound firewall rule needed. Set `CONSORTIUM_CLI_TIMEOUT` (seconds, default
300) if your agent is slow.

### Bring any API key (any OpenAI-compatible provider)

Beyond the built-in `anthropic`/`openai`/`kimi`, the `custom` provider accepts a
key for **any OpenAI-compatible endpoint** — OpenRouter, Groq, Together,
DeepSeek, Fireworks, or a local Ollama/LM Studio/vLLM server:

```bash
python worker/agent_client.py --server https://<tunnel> --owner Erin \
    --provider custom --base-url https://openrouter.ai/api/v1 \
    --model meta-llama/llama-3.1-8b-instruct --api-key sk-or-...
# local model, no key:
python worker/agent_client.py --server http://<ip>:8000 --owner Finn \
    --provider custom --base-url http://localhost:11434/v1 --model llama3.1
```

In the browser join panel, pick **Custom · any OpenAI-compatible** and fill in
the base URL + model. (The `cli` provider is terminal-only — it needs shell
access, so use the Python client for it.)

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