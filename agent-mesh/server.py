"""
Agent Mesh orchestrator — zero-dependency stdlib HTTP server.

One host runs this. Teammates (strangers at a hackathon) open the URL on the
LAN, register the AI they brought, then anyone submits one prompt. The system
has an LLM split the prompt into fixed slots, fans the sub-tasks out to
everyone's agents IN PARALLEL, and merges the pieces into one web page.

Run:  python3 server.py            (listens on 0.0.0.0:8000)
      PORT=9000 python3 server.py  (custom port)

Load keys from a .env file automatically if present (no dependency needed).
"""
from __future__ import annotations

import os
import json
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import agents as agent_mod
from agents import Agent, run_agent
from dispatcher import dispatch, assign_agents_to_slots, merge, SLOT_A, SLOT_B


def _load_dotenv(path=".env"):
    """Minimal .env loader so we don't need python-dotenv."""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

# --- Shared in-memory session state (one room; fine for a hackathon) ---
REGISTRY: dict[str, Agent] = {}
LAST_RESULT = {"html": ""}
LOCK = threading.Lock()
POOL = ThreadPoolExecutor(max_workers=8)

PROVIDER_LABELS = {"anthropic": "Claude", "openai": "Codex", "mock": "Mock"}


def roster():
    with LOCK:
        return [{"id": a.id, "display": a.display, "provider": a.provider}
                for a in REGISTRY.values()]


def register_agent(owner, provider, api_key=None, model=None):
    aid = f"{owner.lower().replace(' ', '-')}-{provider}-{uuid.uuid4().hex[:4]}"
    display = f"{owner}'s {PROVIDER_LABELS.get(provider, provider)}"
    with LOCK:
        REGISTRY[aid] = Agent(id=aid, owner=owner, provider=provider,
                              display=display, api_key=api_key or None, model=model or None)
    return {"id": aid, "display": display, "agents": roster()}


def run_pipeline(prompt, emit):
    """Execute dispatch -> parallel fan-out -> merge, emitting SSE events."""
    with LOCK:
        agents_list = list(REGISTRY.values())
    if not agents_list:
        emit("error", {"message": "No agents registered. Have teammates join first."})
        return

    emit("status", {"message": "Dispatcher is splitting the prompt..."})
    disp = dispatch(prompt)
    slots = disp["slots"]
    mapping = assign_agents_to_slots(agents_list)
    emit("dispatch", {
        "used_llm": disp["used_llm"], "note": disp["note"],
        "assignments": {
            SLOT_A: {"display": mapping[SLOT_A].display, "task": slots[SLOT_A]},
            SLOT_B: {"display": mapping[SLOT_B].display, "task": slots[SLOT_B]},
        },
    })

    # Real parallel fan-out across the agents behind the uniform wrapper.
    emit("status", {"message": "Fanning out to agents in parallel..."})
    futures = {
        SLOT_A: POOL.submit(run_agent, mapping[SLOT_A], slots[SLOT_A], SLOT_A),
        SLOT_B: POOL.submit(run_agent, mapping[SLOT_B], slots[SLOT_B], SLOT_B),
    }
    results = {}
    for slot, fut in futures.items():
        res = fut.result()
        results[slot] = res
        emit("agent_done", {
            "slot": slot, "display": res.display, "ok": res.ok,
            "elapsed": res.elapsed, "error": res.error, "output": res.output[:4000],
        })

    structure = results[SLOT_A].output if results[SLOT_A].ok else \
        "<p style='color:#b00'>[structure agent failed]</p>"
    style = results[SLOT_B].output if results[SLOT_B].ok else \
        "<style>body{font-family:sans-serif;padding:2rem}</style>"
    html = merge(structure, style)
    with LOCK:
        LAST_RESULT["html"] = html
    emit("merged", {"html": html})
    emit("done", {"message": "Build complete."})


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quieter console
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/?"):
            self._send(200, INDEX_HTML, "text/html; charset=utf-8")
        elif self.path == "/api/agents":
            self._send(200, {"agents": roster()})
        elif self.path == "/preview":
            self._send(200, LAST_RESULT["html"] or "<p>No build yet.</p>",
                       "text/html; charset=utf-8")
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        if self.path == "/api/register":
            body = self._read_json()
            if body.get("provider") not in PROVIDER_LABELS:
                return self._send(400, {"error": "unknown provider"})
            if not body.get("owner"):
                return self._send(400, {"error": "owner required"})
            self._send(200, register_agent(body["owner"], body["provider"],
                                            body.get("api_key"), body.get("model")))
        elif self.path.startswith("/api/unregister/"):
            aid = self.path.rsplit("/", 1)[-1]
            with LOCK:
                REGISTRY.pop(aid, None)
            self._send(200, {"agents": roster()})
        elif self.path == "/api/build":
            body = self._read_json()
            prompt = (body.get("prompt") or "").strip()
            if not prompt:
                return self._send(400, {"error": "prompt required"})
            # Stream Server-Sent Events as the pipeline runs.
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            def emit(event, data):
                try:
                    chunk = f"event: {event}\ndata: {json.dumps(data)}\n\n"
                    self.wfile.write(chunk.encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass

            try:
                run_pipeline(prompt, emit)
            except Exception as e:
                emit("error", {"message": f"pipeline crashed: {e}"})
        else:
            self._send(404, {"error": "not found"})


INDEX_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Agent Mesh</title>
<style>
  :root { --bg:#0e1116; --panel:#171c24; --line:#2a313c; --accent:#5b9dff; --ok:#3ecf8e; --err:#ff6b6b; --muted:#8a94a6; }
  * { box-sizing:border-box; }
  body { margin:0; font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif; background:var(--bg); color:#e6e9ef; }
  header { padding:18px 24px; border-bottom:1px solid var(--line); display:flex; align-items:baseline; gap:12px; flex-wrap:wrap; }
  header h1 { font-size:18px; margin:0; } header .sub { color:var(--muted); font-size:13px; }
  .wrap { display:grid; grid-template-columns:320px 1fr; height:calc(100vh - 60px); }
  .side { border-right:1px solid var(--line); padding:18px; overflow:auto; }
  .main { padding:18px; overflow:auto; }
  h2 { font-size:12px; text-transform:uppercase; letter-spacing:.08em; color:var(--muted); margin:0 0 10px; }
  input, select, textarea { width:100%; background:#0b0e13; border:1px solid var(--line); color:#e6e9ef; border-radius:8px; padding:9px 10px; margin-bottom:8px; }
  button { background:var(--accent); color:#06101f; border:none; border-radius:8px; padding:10px 14px; font-weight:600; cursor:pointer; }
  .roster .agent { display:flex; justify-content:space-between; align-items:center; background:var(--panel); border:1px solid var(--line); border-radius:8px; padding:8px 10px; margin-bottom:6px; font-size:13px; }
  .roster .agent small { color:var(--muted); }
  .roster .agent .x { cursor:pointer; color:var(--muted); }
  .status { font-size:13px; color:var(--muted); margin:8px 0; min-height:18px; }
  .panels { display:grid; grid-template-columns:1fr 1fr; gap:12px; margin:14px 0; }
  .card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px; }
  .card h3 { margin:0 0 6px; font-size:13px; }
  .card .who { color:var(--accent); font-weight:600; }
  .card .task { font-size:12px; color:var(--muted); white-space:pre-wrap; max-height:110px; overflow:auto; margin:6px 0; }
  .card pre { background:#0b0e13; border:1px solid var(--line); border-radius:6px; padding:8px; font-size:11px; max-height:200px; overflow:auto; white-space:pre-wrap; }
  .badge { font-size:11px; padding:2px 7px; border-radius:20px; border:1px solid var(--line); }
  .badge.run { color:var(--accent); } .badge.ok { color:var(--ok); border-color:var(--ok); } .badge.err { color:var(--err); border-color:var(--err); }
  iframe { width:100%; height:420px; background:#fff; border:1px solid var(--line); border-radius:10px; }
</style>
</head>
<body>
<header>
  <h1>🕸️ Agent Mesh</h1>
  <span class="sub">one prompt → LLM splits it → fan out to everyone's AI in parallel → merged page</span>
</header>
<div class="wrap">
  <div class="side">
    <h2>Join — bring your AI</h2>
    <input id="owner" placeholder="Your name (e.g. Alice)" />
    <select id="provider">
      <option value="anthropic">Claude (Anthropic)</option>
      <option value="openai">Codex (OpenAI)</option>
      <option value="mock">Mock (no key / testing)</option>
    </select>
    <input id="apikey" placeholder="API key (optional — uses host env if blank)" />
    <button onclick="register()">Register my agent</button>
    <h2 style="margin-top:18px;">Team roster</h2>
    <div id="roster" class="roster"></div>
  </div>
  <div class="main">
    <h2>Build request</h2>
    <textarea id="prompt" rows="3" placeholder="e.g. Build a landing page for a hackathon team-matching app with a hero and a signup form"></textarea>
    <button onclick="build()">🚀 Dispatch to the mesh</button>
    <div id="status" class="status"></div>
    <div id="note" class="status"></div>
    <div class="panels" id="panels"></div>
    <h2>Merged result (live preview)</h2>
    <iframe id="preview"></iframe>
  </div>
</div>
<script>
let roster = [];
async function refresh(){ const r = await (await fetch('/api/agents')).json(); roster=r.agents; drawRoster(); }
function drawRoster(){
  const el=document.getElementById('roster');
  el.innerHTML = roster.length?'':'<small style="color:#8a94a6">No one has joined yet.</small>';
  roster.forEach(a=>{ const d=document.createElement('div'); d.className='agent';
    d.innerHTML=`<span>${a.display}<br><small>${a.provider}</small></span><span class="x" onclick="unreg('${a.id}')">✕</span>`;
    el.appendChild(d); });
}
async function register(){
  const owner=document.getElementById('owner').value.trim();
  const provider=document.getElementById('provider').value;
  const api_key=document.getElementById('apikey').value.trim();
  if(!owner) return alert('Enter your name');
  const r=await (await fetch('/api/register',{method:'POST',headers:{'content-type':'application/json'},
    body:JSON.stringify({owner,provider,api_key})})).json();
  roster=r.agents; drawRoster(); document.getElementById('apikey').value='';
}
async function unreg(id){ const r=await (await fetch('/api/unregister/'+id,{method:'POST'})).json(); roster=r.agents; drawRoster(); }
function panelFor(slot,title){
  let p=document.getElementById('panel-'+slot);
  if(!p){ p=document.createElement('div'); p.className='card'; p.id='panel-'+slot;
    p.innerHTML=`<h3>${title} — <span class="who" id="who-${slot}">…</span> <span class="badge run" id="badge-${slot}">queued</span></h3>
      <div class="task" id="task-${slot}"></div><pre id="out-${slot}"></pre>`;
    document.getElementById('panels').appendChild(p); }
  return p;
}
function build(){
  document.getElementById('panels').innerHTML='';
  document.getElementById('status').textContent='';
  document.getElementById('note').textContent='';
  const prompt=document.getElementById('prompt').value.trim();
  if(!prompt) return alert('Enter a build request');
  fetch('/api/build',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({prompt})})
    .then(res=>{ const reader=res.body.getReader(); const dec=new TextDecoder(); let buf='';
      function pump(){ return reader.read().then(({done,value})=>{
        if(done) return;
        buf+=dec.decode(value,{stream:true});
        let parts=buf.split('\n\n'); buf=parts.pop();
        parts.forEach(chunk=>{ const ev=/event: (.*)/.exec(chunk); const dt=/data: (.*)/s.exec(chunk);
          if(ev&&dt) handle(ev[1],JSON.parse(dt[1])); });
        return pump(); }); }
      return pump(); });
}
function handle(ev,d){
  const status=document.getElementById('status');
  if(ev==='status') status.textContent=d.message;
  else if(ev==='error') status.textContent='⚠️ '+d.message;
  else if(ev==='dispatch'){
    document.getElementById('note').textContent=(d.used_llm?'🧠 ':'⚙️ ')+d.note;
    const map={structure:'Structure + JS', style:'Styling + Content'};
    for(const slot of ['structure','style']){
      panelFor(slot,map[slot]);
      document.getElementById('who-'+slot).textContent=d.assignments[slot].display;
      document.getElementById('task-'+slot).textContent=d.assignments[slot].task;
      const b=document.getElementById('badge-'+slot); b.textContent='running'; b.className='badge run';
    }
  }
  else if(ev==='agent_done'){
    const b=document.getElementById('badge-'+d.slot);
    b.textContent=d.ok?('✓ '+d.elapsed+'s'):'✗ failed'; b.className='badge '+(d.ok?'ok':'err');
    document.getElementById('out-'+d.slot).textContent=d.ok?d.output:d.error;
  }
  else if(ev==='merged') document.getElementById('preview').srcdoc=d.html;
  else if(ev==='done') status.textContent='✅ '+d.message;
}
refresh(); setInterval(refresh,4000);
</script>
</body>
</html>
"""


def main():
    port = int(os.getenv("PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"🕸️  Agent Mesh running at http://0.0.0.0:{port}")
    print(f"    Teammates on the same network join at  http://<your-LAN-ip>:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


if __name__ == "__main__":
    main()
