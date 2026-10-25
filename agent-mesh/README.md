# 🕸️ Agent Mesh

**One prompt → an LLM splits it → the sub-tasks fan out in parallel to every
teammate's AI → the pieces merge into one web page.**

Built for hackathons: you show up, everyone has a *different* AI (Claude, Codex,
…), and it's hard to work together. Agent Mesh turns the whole team's assistants
into one coordinated worker pool behind a single prompt.

## Why it's robust for a live demo

- **Zero dependencies.** Pure Python standard library — no `pip install`, runs on
  any laptop with Python 3.10+. (Great when you're on random hackathon wifi.)
- **Heterogeneous by design.** Each agent sits behind one uniform `run_agent()`
  wrapper, so a stranger's AI slots in by picking a provider — no code changes.
- **Real parallelism.** Agents run concurrently in a thread pool (verified: two
  1s agents finish in ~1s, not 2s).
- **Graceful degradation.** The LLM dispatcher falls back to a deterministic
  fixed split if it fails; a failed/key-less agent degrades its slot instead of
  crashing the build. **The demo never fully dies on one bad call.**
- **The collaboration is visible.** Live panels show each named agent, its
  assigned sub-task, status, timing, and output — then the merged page renders.

## Architecture

```
team prompt
   │
   ▼
[LLM dispatcher]  → JSON { "structure": ..., "style": ... }   (fixed slots)
   │  └─ falls back to deterministic fixed split on any failure
   ▼
parallel fan-out ──► slot "structure"  →  Agent A  (HTML + JS)
                └──► slot "style"       →  Agent B  (CSS + content)
   │
   ▼
merge → one standalone HTML document
   │
   ▼
live UI: prompt box → per-agent panels → rendered preview
```

Files:
- `agents.py` — uniform wrapper over Anthropic / OpenAI / mock providers.
- `dispatcher.py` — LLM splitter (strict JSON) + fixed-split fallback + merge.
- `server.py` — stdlib HTTP server, SSE streaming, join/roster/build endpoints, UI.
- `selftest.py` — in-process end-to-end test (run it to prove the pipeline works).

## Run it

```bash
# optional: give the host keys so blank-key agents just work
export ANTHROPIC_API_KEY=sk-ant-...
export OPENAI_API_KEY=sk-...        # also powers the LLM dispatcher

python3 server.py                   # http://0.0.0.0:8000
# custom port:  PORT=9000 python3 server.py
```

A `.env` file (same dir) is auto-loaded, no library needed:

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
```

## Demo across your 3 computers

1. **Host laptop:** `python3 server.py`, note its LAN IP (`ipconfig` / `ifconfig`).
2. **Each teammate's laptop:** open `http://<host-LAN-ip>:8000`, enter a name,
   pick the AI they brought, paste their key (or leave blank to use host env),
   click **Register**. They appear in everyone's roster live.
3. **Anyone:** type one build request, hit **🚀 Dispatch to the mesh**.
4. Watch the sub-tasks fan out to the different agents in parallel and the page
   assemble live.

> No keys handy while testing? Register agents as **Mock** — the full flow
> (split → parallel fan-out → merge → render) runs end-to-end with canned output.

## Prove it works (no keys, no network needed)

```bash
python3 selftest.py      # runs the whole pipeline in-process, writes sample_output.html
```

## 60-second pitch

> "At hackathons everyone brings a different AI and they work in silos. Agent Mesh
> takes **one** team prompt, has an LLM split it into tasks, and dispatches those
> to **everyone's** AI in parallel — Alice's Claude builds the structure while
> Bob's Codex builds the styling — then merges the results into one page. Your
> whole team's assistants become a single coordinated worker pool."

## Extending past the MVP

- More slots / agents: `dispatcher.py` slots + `assign_agents_to_slots` already
  round-robin extra agents; the merge is the only part that assumes two slots.
- New provider: add one `_call_x()` in `agents.py` and a branch in `run_agent`.
- Smarter merge: replace `merge()` with an "assembler" LLM call.
