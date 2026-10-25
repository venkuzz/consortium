# Consortium — leader-driven multi-AI collaboration

A leader turns one project goal into tasks. Teammates connect the AI they
already use, workers pull eligible tasks, and results return to the leader for
review. Accepted task results become context for dependent tasks.

The current prototype uses one shared, in-memory project and Python's standard
library. It has no build step or third-party runtime dependencies.

## Start the leader

From the `agent-mesh` directory:

```bash
python -m server.app
```

The server prints the local and LAN addresses:

```text
Leader (this machine):  http://localhost:8000
Teammates on the LAN:   http://192.168.1.20:8000
Reachability check:     http://192.168.1.20:8000/api/health
```

Open `http://localhost:8000`. Teammates must use the LAN or tunnel address;
`localhost` on their computer points back to their own computer.

Optional `.env` settings:

```dotenv
LEAD_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
KIMI_API_KEY=...
KIMI_BASE_URL=https://...
CONSORTIUM_ROOM_CODE=hunter2
PORT=8000
```

Without a Lead AI key, Consortium uses its deterministic template planner so
the demo can still run.

## Connect a worker

The fastest path is the UI:

1. Select an available task.
2. Click **Connect CLI**.
3. Enter the teammate's name and one-shot AI command.
4. Copy the generated two-line command to the teammate.

The command downloads one self-contained worker file and runs it from any
folder. For coding tasks, the teammate should run it from their own project
checkout or worktree; the CLI agent edits that working directory. The teammate
does not need the Consortium repository.

Claude Code:

```bash
python -c "import urllib.request; urllib.request.urlretrieve('http://LEADER:8000/worker', 'consortium_worker.py')"
python consortium_worker.py --server http://LEADER:8000 --owner Alice --provider cli --cli-cmd "claude -p" --long-poll 20
```

Codex CLI:

```bash
python consortium_worker.py --server http://LEADER:8000 --owner Alice --provider cli --cli-cmd "codex exec --full-auto -" --long-poll 20
```

Aider:

```bash
python consortium_worker.py --server http://LEADER:8000 --owner Alice --provider cli --cli-cmd "aider --message-file -" --long-poll 20
```

The CLI must support one-shot operation: read the task from stdin, print its
result to stdout, and exit. It keeps its own login; no model API key is sent to
the leader.

The full-repository client remains available:

```bash
python worker/agent_client.py --server http://LEADER:8000 --owner Alice \
  --provider cli --cli-cmd "claude -p" --long-poll 20
```

Workers can also call a model API directly. Their keys stay on their machine:

```bash
python consortium_worker.py --server http://LEADER:8000 --owner Alice \
  --provider anthropic --api-key sk-ant-...

python consortium_worker.py --server http://LEADER:8000 --owner Alice \
  --provider custom --base-url https://openrouter.ai/api/v1 \
  --model provider/model --api-key sk-...
```

Use `--provider mock` to test the workflow without an AI.

## Run a project

1. The leader enters a goal and clicks **Plan & launch**.
2. Workers claim eligible tasks atomically. A task waits until all dependencies
   have been accepted.
3. Every worker receives the project summary and accepted dependency outputs.
4. The leader accepts a result or rejects it with a follow-up instruction.
5. Assigning a rejected task retries it as a clean queued attempt.
6. Marking the project done stops new claims.

Workers send a heartbeat during long AI calls. If one disappears, its active
task returns to the queue and every connected dashboard refreshes.

## LAN and public sharing

On Windows, allow inbound traffic to the selected port in Administrator
PowerShell:

```powershell
New-NetFirewallRule -DisplayName "Consortium 8000" `
  -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

Confirm reachability from the worker computer:

```powershell
Test-NetConnection 192.168.1.20 -Port 8000
curl http://192.168.1.20:8000/api/health
```

For teammates outside the LAN, the included PowerShell script starts the server
and a temporary Cloudflare quick tunnel:

```powershell
powershell -ExecutionPolicy Bypass -File .\share.ps1
```

The printed `trycloudflare.com` URL is public until the script stops and changes
on restart. Set a room code for a shared demo:

```powershell
powershell -ExecutionPolicy Bypass -File .\share.ps1 -RoomCode hunter2
```

## Architecture

```text
leader browser ── HTTP + SSE ──► server/app.py
                                     │
                                     ├── core/store.py       task state
                                     ├── lead/               planning/review
                                     └── /worker             worker download

worker command ── long poll ───► /api/worker/{id}/next
worker AI      ◄── task + dependency outputs
worker command ── result ──────► /api/worker/{id}/submit
```

The server stores one project in memory. Restarting it clears the project,
workers, and tasks.

## API smoke check

```bash
curl http://localhost:8000/api/health
curl http://localhost:8000/api/project
curl -X POST http://localhost:8000/api/project -d '{"goal":"demo landing page"}'
curl -X POST http://localhost:8000/api/project/plan -d '{}'
curl http://localhost:8000/api/tasks
```

The frontend lives in `server/webui/`. The live event stream is
`GET /api/events`.

## Tests

Run from `agent-mesh`:

```bash
python -m tests.test_store
python -m tests.test_server_routes
python -m tests.test_e2e
python -m tests.test_webui_serving
python -m tests.test_cli_provider
python -m tests.test_assignment
python -m tests.test_orchestrator
python -m tests.test_workflow_regressions
```
