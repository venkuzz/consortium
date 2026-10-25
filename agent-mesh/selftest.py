"""In-process end-to-end test of the pipeline (no sockets needed)."""
import os
os.environ.pop("OPENAI_API_KEY", None)  # force deterministic fixed-split path

from agents import Agent, run_agent
from dispatcher import dispatch, assign_agents_to_slots, merge, SLOT_A, SLOT_B
import server

print("=== 1. Register two agents via server logic ===")
server.register_agent("Alice", "mock")
server.register_agent("Bob", "mock")
print("roster:", [a["display"] for a in server.roster()])
assert len(server.roster()) == 2

print("\n=== 2. Run the full pipeline, capturing SSE events ===")
events = []
server.run_pipeline("Build a landing page with a hero and a signup form",
                    lambda ev, d: events.append((ev, d)))

names = [e[0] for e in events]
print("event sequence:", names)
assert names[0] == "status"
assert "dispatch" in names
assert names.count("agent_done") == 2
assert "merged" in names
assert names[-1] == "done"

disp_evt = next(d for ev, d in events if ev == "dispatch")
print("used_llm:", disp_evt["used_llm"], "| note:", disp_evt["note"])
print("slot A ->", disp_evt["assignments"][SLOT_A]["display"])
print("slot B ->", disp_evt["assignments"][SLOT_B]["display"])

for ev, d in events:
    if ev == "agent_done":
        print(f"  agent_done: {d['display']:20} ok={d['ok']} elapsed={d['elapsed']}s")
        assert d["ok"], d["error"]

merged = next(d for ev, d in events if ev == "merged")["html"]
print("\n=== 3. Validate merged HTML ===")
assert merged.startswith("<!DOCTYPE html>")
assert "<style" in merged.lower(), "merged page has no CSS"
assert "<header" in merged.lower() and "<main" in merged.lower(), "structure missing"
assert "join" in merged.lower() or "sign" in merged.lower(), "signup element missing"
print("merged length:", len(merged), "chars — contains <style>, <header>, <main>, signup control")

print("\n=== 4. Parallelism check (two 1s mock calls should overlap) ===")
import time
from concurrent.futures import ThreadPoolExecutor
a = Agent("x", "X", "mock", "X")
b = Agent("y", "Y", "mock", "Y")
t0 = time.time()
with ThreadPoolExecutor(max_workers=4) as ex:
    list(ex.map(lambda ag: run_agent(ag, "task", "structure"), [a, b]))
dt = time.time() - t0
print(f"two 1.0s mock agents in parallel took {dt:.2f}s (serial would be ~2.0s)")
assert dt < 1.7, "agents did not run in parallel!"

print("\n=== 5. Graceful failure: real provider, no key ===")
res = run_agent(Agent("z", "Z", "openai", "Z's Codex"), "hi")
print("ok:", res.ok, "| error:", res.error)
assert res.ok is False and "No API key" in res.error

print("\nALL SELFTESTS PASSED ✅")

# Write the merged page to disk so it can be viewed in the file explorer.
with open("sample_output.html", "w") as f:
    f.write(merged)
print("wrote sample_output.html for visual inspection")
