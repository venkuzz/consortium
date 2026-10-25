/* Consortium Mission Control — vanilla JS, zero deps.
 * Codes against WORK_SPLIT.md §4 only (HTTP + SSE).
 *
 * Architecture:
 *   - STATE mirrors the server snapshot; SSE events mutate it incrementally.
 *   - render*() functions paint the DOM decks from STATE.
 *   - The canvas constellation is an independent physics loop reading STATE:
 *       Lead core (center)  ·  worker nodes (orbiting)  ·  task nodes (ring)
 *       edges = task dependencies  ·  pulses = live claim/submit/review events.
 */
(() => {
"use strict";

// ───────────────────────── state ─────────────────────────
const STATE = {
  project: null,
  tasks: new Map(),      // id -> task
  workers: new Map(),    // id -> worker
  stats: {},
  myWorkerId: null,
};
const STATE_COLORS = {
  queued:"#8793a9", assigned:"#5b9dff", submitted:"#ffb454",
  accepted:"#3ecf8e", rejected:"#ff6b6b",
};

const $ = (s) => document.querySelector(s);
const api = async (method, path, body) => {
  const opt = { method, headers: {} };
  if (body !== undefined) { opt.headers["content-type"] = "application/json"; opt.body = JSON.stringify(body); }
  const res = await fetch(path, opt);
  let data = null; try { data = await res.json(); } catch {}
  if (!res.ok) throw new Error((data && data.error) || `HTTP ${res.status}`);
  return data;
};

// ───────────────────────── SSE wiring ─────────────────────────
let es = null;
function connect() {
  es = new EventSource("/api/events");
  const dot = $("#conn-dot"), txt = $("#conn-text");
  es.onopen = () => { dot.className = "conn-dot live"; txt.textContent = "live"; };
  es.onerror = () => {
    dot.className = "conn-dot down"; txt.textContent = "reconnecting…";
    // EventSource auto-reconnects; it will re-send a snapshot on reopen.
  };
  const on = (name, fn) => es.addEventListener(name, (e) => fn(JSON.parse(e.data)));

  on("snapshot", (d) => {
    STATE.project = d.project;
    STATE.tasks = new Map((d.tasks||[]).map(t => [t.id, t]));
    STATE.workers = new Map((d.workers||[]).map(w => [w.id, w]));
    STATE.stats = d.stats || {};
    renderAll();
    sim.sync();
  });
  on("project_state", (d) => {
    if (STATE.project) { STATE.project.state = d.state; STATE.project.summary = d.summary; }
    renderPhase(); renderCounters(); tick(`phase → <b>${d.state}</b>`, "ok");
  });
  on("tasks_planned", (d) => {
    (d.tasks||[]).forEach(t => STATE.tasks.set(t.id, t));
    renderCounters(); sim.sync();
    tick(`lead planned <b>${(d.tasks||[]).length}</b> tasks`, "ok");
  });
  on("task_created", (d) => { STATE.tasks.set(d.task.id, d.task); renderCounters(); sim.sync(); });
  on("task_claimed", (d) => {
    const t = STATE.tasks.get(d.task_id); if (t){ t.state="assigned"; t.assigned_to=d.worker_id; }
    const w = STATE.workers.get(d.worker_id); if (w){ w.status="working"; w.current_task_id=d.task_id; }
    renderCounters(); renderRoster(); sim.pulse(d.worker_id, d.task_id, "#5b9dff");
    tick(`${who(d.worker_id)} claimed <b>${shortTitle(d.task_id)}</b>`);
  });
  on("task_submitted", (d) => {
    const t = STATE.tasks.get(d.task_id); if (t) t.state="submitted";
    const w = STATE.workers.get(d.worker_id); if (w){ w.status="idle"; w.current_task_id=null; }
    renderCounters(); renderRoster(); renderReviewQueue();
    sim.pulse(d.task_id, d.worker_id, d.ok ? "#ffb454" : "#ff6b6b");
    tick(`${who(d.worker_id)} submitted <b>${shortTitle(d.task_id)}</b>`, d.ok?"warn":"err");
  });
  on("task_reviewed", (d) => {
    const t = STATE.tasks.get(d.task_id); if (t){ t.state=d.decision; t.review_note=d.note||""; }
    renderCounters(); renderReviewQueue(); sim.sync();
    const good = d.decision === "accepted";
    sim.burst(d.task_id, good ? "#3ecf8e" : "#ff6b6b");
    tick(`lead <b>${d.decision}</b> ${shortTitle(d.task_id)}`, good?"ok":"err");
    refreshInspectorIf(d.task_id);
  });
  on("worker_joined", (d) => { STATE.workers.set(d.worker.id, d.worker); renderRoster(); sim.sync(); tick(`<b>${d.worker.display}</b> joined the mesh`, "ok"); });
  on("worker_left", (d) => { const w=STATE.workers.get(d.worker_id); STATE.workers.delete(d.worker_id); renderRoster(); sim.sync(); if(w) tick(`${w.display} left`, "err"); });
  on("done", (d) => { if(STATE.project) STATE.project.state="done"; renderPhase(); tick(`mission complete — <b>${d.summary||"done"}</b>`, "ok"); sim.celebrate(); });
}
const who = (id) => { const w=STATE.workers.get(id); return `<b>${w?w.display:id}</b>`; };
const shortTitle = (id) => { const t=STATE.tasks.get(id); return t?t.title:id; };

// ───────────────────────── render: phase + counters ─────────────────────────
function renderAll(){ renderPhase(); renderCounters(); renderRoster(); renderReviewQueue(); fillGoal(); }
function fillGoal(){ if (STATE.project && !$("#goal").value) $("#goal").value = STATE.project.goal || ""; }

function renderPhase(){
  const st = STATE.project ? STATE.project.state : "none";
  const pill = $("#phase-pill"); pill.dataset.state = st;
  $("#phase-label").textContent = STATE.project ? st : "no project";
  const active = STATE.project && st === "active";
  const paused = STATE.project && st === "paused";
  const live = STATE.project && (st === "active" || st === "paused");
  $("#btn-pause").disabled  = !active;
  $("#btn-resume").disabled = !paused;
  $("#btn-done").disabled   = !live;
  $("#btn-addtask").disabled = !live;
}
function renderCounters(){
  const s = STATE.stats || {};
  ["queued","assigned","submitted","accepted","rejected"].forEach(k=>{
    const el = $("#c-"+k); if(!el) return;
    const n = countState(k);
    if (el.textContent !== String(n)) {
      el.textContent = n;
      const c = el.closest(".counter"); c.classList.remove("bump"); void c.offsetWidth; c.classList.add("bump");
    }
  });
  const online = [...STATE.workers.values()].filter(w=>w.status!=="offline").length;
  STATE.stats.workers_online = online;
}
const countState = (k) => [...STATE.tasks.values()].filter(t=>t.state===k).length;

function renderRoster(){
  const el = $("#roster"), ws = [...STATE.workers.values()];
  $("#roster-count").textContent = ws.length;
  if (!ws.length){ el.innerHTML = '<div class="empty">No minds yet. Bring yours →</div>'; return; }
  el.innerHTML = "";
  ws.forEach(w=>{
    const d = document.createElement("div"); d.className = "agent";
    const col = providerColor(w.provider);
    const me = w.id === STATE.myWorkerId ? " · you" : "";
    d.innerHTML = `<span class="av" style="background:${col}">${(w.owner||w.display||"?")[0].toUpperCase()}</span>
      <span class="meta"><b>${esc(w.display)}${me}</b><small>${w.provider}${w.current_task_id?" · "+esc(shortTitle(w.current_task_id)):""}</small></span>
      <span class="wstat ${w.status}">${w.status}</span>`;
    el.appendChild(d);
  });
}
function renderReviewQueue(){
  const el = $("#review-queue");
  const subs = [...STATE.tasks.values()].filter(t=>t.state==="submitted").sort((a,b)=>a.seq-b.seq);
  $("#review-count").textContent = subs.length;
  if (!subs.length){ el.innerHTML = '<div class="empty">Nothing awaiting review.</div>'; return; }
  el.innerHTML = "";
  subs.forEach(t=>{
    const c = document.createElement("div"); c.className="rev-card";
    c.innerHTML = `<b>${esc(t.title)}</b><small>by ${esc((STATE.workers.get(t.assigned_to)||{}).display||t.assigned_to||"—")}</small>`;
    c.onclick = () => openInspector(t.id);
    el.appendChild(c);
  });
}

// ───────────────────────── leader + worker actions ─────────────────────────
$("#btn-create").onclick = async () => {
  const goal = $("#goal").value.trim(); if(!goal) return note("#plan-note","enter a goal first");
  try { await api("POST","/api/project",{goal}); note("#plan-note","goal set — ready to plan"); }
  catch(e){ note("#plan-note","⚠ "+e.message); }
};
$("#btn-plan").onclick = async () => {
  const goal = $("#goal").value.trim();
  try {
    if (!STATE.project) { if(!goal) return note("#plan-note","enter a goal first"); await api("POST","/api/project",{goal}); }
    note("#plan-note","lead is planning…");
    const r = await api("POST","/api/project/plan", goal?{goal}:{});
    note("#plan-note", (r.used_llm?"🧠 ":"⚙ ")+(r.note||"planned"));
  } catch(e){ note("#plan-note","⚠ "+e.message); }
};
$("#btn-pause").onclick  = () => setState("paused");
$("#btn-resume").onclick = () => setState("active");
$("#btn-done").onclick   = () => { if(confirm("Mark the mission done? Workers will stop pulling tasks.")) setState("done"); };
async function setState(s){ try{ await api("POST","/api/project/state",{state:s}); }catch(e){ note("#plan-note","⚠ "+e.message);} }

$("#btn-addtask").onclick = async () => {
  const title=$("#t-title").value.trim(), instruction=$("#t-instruction").value.trim();
  if(!title||!instruction) return;
  try{ await api("POST","/api/tasks",{title,instruction}); $("#t-title").value=""; $("#t-instruction").value=""; }
  catch(e){ note("#plan-note","⚠ "+e.message); }
};

$("#btn-join").onclick = async () => {
  const owner=$("#w-owner").value.trim(), provider=$("#w-provider").value, api_key=$("#w-key").value.trim();
  const base_url=$("#w-base-url").value.trim(), model=$("#w-model").value.trim();
  if(!owner) return note("#join-note","enter your name");
  if(provider==="custom" && !base_url) return note("#join-note","custom needs a base URL");
  try{
    const r = await api("POST","/api/worker/register",{owner,provider,api_key,model,base_url});
    STATE.myWorkerId = r.worker.id; $("#w-key").value="";
    note("#join-note",`joined as ${r.worker.display} ✦`);
    startWorkerLoop(r.worker.id, provider, api_key, base_url, model);
  }catch(e){ note("#join-note","⚠ "+e.message); }
};

// Show base-URL / model fields only for the custom provider.
$("#w-provider").onchange = () => {
  const p = $("#w-provider").value;
  $("#w-custom-url-field").style.display = p==="custom" ? "" : "none";
  $("#w-model-field").style.display = (p==="custom"||p==="kimi") ? "" : "none";
};

/* Browser worker loop. For mock it generates output locally; for real
 * providers the key never leaves the browser — we call the provider directly
 * from here, else fall back to a stub so the demo keeps flowing. */
async function startWorkerLoop(wid, provider, key, baseUrl, model){
  let stop = false;
  const hb = setInterval(()=>api("POST",`/api/worker/${wid}/heartbeat`).catch(()=>{}), 5000);
  async function loop(){
    while(!stop){
      let r;
      try { r = await api("POST",`/api/worker/${wid}/next`); }
      catch { await sleep(2500); continue; }
      if (r.stop){ break; }
      if (!r.task){ await sleep(2200); continue; }
      const t0 = performance.now();
      const output = await executeTask(provider, key, baseUrl, model, r.task, r.project);
      const elapsed = +((performance.now()-t0)/1000).toFixed(2);
      try { await api("POST",`/api/worker/${wid}/submit`,{task_id:r.task.id, output, ok:true, elapsed}); } catch {}
    }
    clearInterval(hb);
  }
  loop();
}

// Base URLs for the OpenAI-compatible providers (custom supplies its own).
const PROVIDER_BASE = {
  openai:"https://api.openai.com/v1",
  kimi:"https://api.moonshot.ai/v1",
};
const DEFAULT_MODEL = { openai:"gpt-4o", kimi:"moonshot-v1-8k", custom:"gpt-3.5-turbo" };

async function executeTask(provider, key, baseUrl, model, task, project){
  // No key, or mock → believable local output after a short "think".
  if (provider==="mock" || !key){
    await sleep(900 + Math.random()*1400);
    return `<!-- ${task.title} · generated locally (${provider}${key?"":", no key"}) -->\n`+
           `<section id="${slug(task.title)}">\n  <!-- ${task.instruction.slice(0,120)} -->\n`+
           `  <p>Deliverable for: ${esc(project?project.goal:"mission")}</p>\n</section>`;
  }
  const sys = "You are a worker AI on a collaborative build team. Produce ONLY the "+
              "deliverable the instruction asks for — no preamble, no explanation.";
  const user = `Project goal: ${project?project.goal:""}\nTask: ${task.title}\n`+
               `Instruction:\n${task.instruction}`;
  try {
    if (provider==="anthropic"){
      // Anthropic Messages API (browser-direct).
      const res = await fetch("https://api.anthropic.com/v1/messages",{method:"POST",
        headers:{"x-api-key":key,"anthropic-version":"2023-06-01","content-type":"application/json",
                 "anthropic-dangerous-direct-browser-access":"true"},
        body:JSON.stringify({model:model||"claude-sonnet-4-5",max_tokens:4096,system:sys,
                             messages:[{role:"user",content:user}]})});
      if(!res.ok) throw new Error("HTTP "+res.status+" "+(await res.text()).slice(0,160));
      const d = await res.json();
      return (d.content||[]).filter(b=>b.type==="text").map(b=>b.text).join("").trim() || "[empty]";
    }
    // openai / kimi / custom → OpenAI-compatible chat/completions.
    const base = provider==="custom" ? baseUrl : PROVIDER_BASE[provider];
    const res = await fetch(base.replace(/\/$/,"")+"/chat/completions",{method:"POST",
      headers:{"Authorization":"Bearer "+key,"content-type":"application/json"},
      body:JSON.stringify({model:model||DEFAULT_MODEL[provider]||"gpt-3.5-turbo",temperature:0.3,
                           messages:[{role:"system",content:sys},{role:"user",content:user}]})});
    if(!res.ok) throw new Error("HTTP "+res.status+" "+(await res.text()).slice(0,160));
    const d = await res.json();
    return (d.choices?.[0]?.message?.content || "").trim() || "[empty]";
  } catch(e){
    // Degrade gracefully: return a note so the lead can reject/requeue.
    return `[worker error from ${provider}] ${String(e.message||e).slice(0,200)}`;
  }
}

// ───────────────────────── inspector ─────────────────────────
let inspId = null;
function openInspector(id){
  const t = STATE.tasks.get(id); if(!t) return; inspId = id;
  $("#insp-seq").textContent = "#"+t.seq;
  $("#insp-title").textContent = t.title;
  const sc = $("#insp-state"); sc.textContent = t.state; sc.dataset.s = t.state;
  $("#insp-instruction").textContent = t.instruction || "—";
  // deps
  const depSec = $("#insp-deps-section"), depEl = $("#insp-deps");
  if (t.depends_on && t.depends_on.length){
    depSec.classList.remove("hidden"); depEl.innerHTML="";
    t.depends_on.forEach(d=>{ const dep=STATE.tasks.get(d); const c=document.createElement("span");
      c.className="dep-chip"; c.textContent=dep?dep.title:d; c.onclick=()=>openInspector(d); depEl.appendChild(c); });
  } else depSec.classList.add("hidden");
  // result
  const resSec = $("#insp-result-section");
  if (t.result){ resSec.classList.remove("hidden"); $("#insp-result").textContent = t.result; }
  else resSec.classList.add("hidden");
  // review controls only for submitted
  $("#insp-review").classList.toggle("hidden", t.state!=="submitted");
  $("#insp-note").value = "";
  $("#inspector").classList.remove("hidden");
}
function refreshInspectorIf(id){ if(inspId===id && !$("#inspector").classList.contains("hidden")) openInspector(id); }
$("#insp-close").onclick = () => $("#inspector").classList.add("hidden");
$("#insp-accept").onclick = async () => {
  if(!inspId) return; const note=$("#insp-note").value.trim();
  try{ await api("POST",`/api/tasks/${inspId}/review`,{decision:"accepted",note}); $("#inspector").classList.add("hidden"); }catch(e){ alert(e.message); }
};
$("#insp-reject").onclick = async () => {
  if(!inspId) return; const note=$("#insp-note").value.trim();
  const followup = prompt("Follow-up instruction for the redo (blank = just reject):","");
  try{ await api("POST",`/api/tasks/${inspId}/review`,{decision:"rejected",note,followup_instruction:followup||undefined}); $("#inspector").classList.add("hidden"); }catch(e){ alert(e.message); }
};
document.addEventListener("keydown",(e)=>{ if(e.key==="Escape") $("#inspector").classList.add("hidden"); });

// deck collapse
document.querySelectorAll(".deck-toggle").forEach(b=>{
  b.onclick = () => { const deck = b.closest(".deck"); deck.classList.toggle("collapsed");
    b.textContent = deck.classList.contains("collapsed") ? (b.dataset.side==="left"?"›":"‹") : (b.dataset.side==="left"?"‹":"›"); };
});

// ───────────────────────── ticker ─────────────────────────
function tick(html, kind){
  const t = $("#ticker"); const line = document.createElement("div");
  line.className = "line" + (kind?` ev-${kind}`:"");
  line.innerHTML = html; t.innerHTML=""; t.appendChild(line);
}

// ───────────────────────── helpers ─────────────────────────
const sleep = (ms)=>new Promise(r=>setTimeout(r,ms));
const esc = (s)=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const slug = (s)=>String(s||"").toLowerCase().replace(/[^a-z0-9]+/g,"-").replace(/^-|-$/g,"")||"section";
function note(sel,msg){ $(sel).textContent = msg; }
function providerColor(p){ return {anthropic:"#d97757",openai:"#3ecf8e",kimi:"#c084fc",mock:"#8793a9"}[p]||"#5b9dff"; }

// ═════════════════════════ CONSTELLATION (canvas physics) ═════════════════════════
const sim = (() => {
  const cv = $("#constellation"), ctx = cv.getContext("2d");
  let W=0,H=0,DPR=Math.min(2,window.devicePixelRatio||1);
  const nodes = new Map();   // id -> {id,type,x,y,vx,vy,r,label,color}
  const pulses = [];         // {fromId,toId,t,speed,color}
  const bursts = [];         // {x,y,t,color}
  let confetti = [];
  let core = { x:0, y:0 };

  function resize(){ W=cv.clientWidth=innerWidth; H=cv.clientHeight=innerHeight;
    cv.width=W*DPR; cv.height=H*DPR; ctx.setTransform(DPR,0,0,DPR,0,0); core={x:W/2,y:H/2}; }
  addEventListener("resize", resize); resize();

  function ensure(id, type, label, color){
    let n = nodes.get(id);
    if(!n){ const a=Math.random()*Math.PI*2, d=type==="worker"?210:330;
      n = { id,type,label,color, x:core.x+Math.cos(a)*d, y:core.y+Math.sin(a)*d, vx:0, vy:0,
            r: type==="worker"?15:9 };
      nodes.set(id,n);
    } else { n.label=label; n.color=color; }
    return n;
  }
  // Rebuild node set from STATE (adds new, removes gone).
  function sync(){
    const live = new Set();
    STATE.workers.forEach(w=>{ live.add("w:"+w.id); ensure("w:"+w.id,"worker",w.display,providerColor(w.provider)); });
    STATE.tasks.forEach(t=>{ live.add("t:"+t.id); const n=ensure("t:"+t.id,"task",t.title,STATE_COLORS[t.state]||"#8793a9"); n.taskState=t.state; });
    [...nodes.keys()].forEach(k=>{ if(k!=="core" && !live.has(k)) nodes.delete(k); });
  }
  function nodeFor(idLabel){ // accept raw worker/task ids from events
    return nodes.get("w:"+idLabel) || nodes.get("t:"+idLabel);
  }
  function pulse(fromRaw, toRaw, color){
    const a=nodeFor(fromRaw), b=nodeFor(toRaw); if(!a||!b) return;
    pulses.push({a,b,t:0,speed:.028,color});
  }
  function burst(taskRaw, color){ const n=nodeFor(taskRaw); if(n) bursts.push({x:n.x,y:n.y,t:0,color}); }
  function celebrate(){ for(let i=0;i<120;i++) confetti.push({x:core.x,y:core.y,
    vx:(Math.random()-.5)*7, vy:(Math.random()-.5)*7-1, t:0, c:["#5b9dff","#3ecf8e","#ffb454","#c084fc"][i%4]}); }

  // physics: workers orbit core; tasks settle on outer ring near their worker/deps.
  function step(){
    const K_CENTER=0.0016, REPEL=1400, DAMP=0.86;
    const arr=[...nodes.values()];
    for(const n of arr){
      // pull toward core at a type-based radius
      const tr = n.type==="worker"?200:320;
      const dx=n.x-core.x, dy=n.y-core.y, dist=Math.hypot(dx,dy)||1;
      const diff=(dist-tr);
      n.vx -= (dx/dist)*diff*K_CENTER; n.vy -= (dy/dist)*diff*K_CENTER;
      // gentle tangential drift so it feels alive
      n.vx += (-dy/dist)*0.015*(n.type==="worker"?1:0.4);
      n.vy += ( dx/dist)*0.015*(n.type==="worker"?1:0.4);
    }
    // mutual repulsion
    for(let i=0;i<arr.length;i++) for(let j=i+1;j<arr.length;j++){
      const a=arr[i],b=arr[j], dx=a.x-b.x, dy=a.y-b.y; let d2=dx*dx+dy*dy; if(d2<1)d2=1;
      const f=REPEL/d2, d=Math.sqrt(d2), fx=(dx/d)*f, fy=(dy/d)*f;
      a.vx+=fx; a.vy+=fy; b.vx-=fx; b.vy-=fy;
    }
    for(const n of arr){ n.vx*=DAMP; n.vy*=DAMP; n.x+=n.vx; n.y+=n.vy; }
  }

  function draw(){
    step();
    ctx.clearRect(0,0,W,H);

    // dependency edges (task -> task)
    ctx.lineWidth=1;
    STATE.tasks.forEach(t=>{
      const to=nodes.get("t:"+t.id); if(!to||!t.depends_on) return;
      t.depends_on.forEach(d=>{ const from=nodes.get("t:"+d); if(!from) return;
        ctx.strokeStyle="rgba(120,150,220,.14)"; ctx.beginPath(); ctx.moveTo(from.x,from.y); ctx.lineTo(to.x,to.y); ctx.stroke(); });
    });
    // worker -> core tethers + task-of-worker link
    nodes.forEach(n=>{ if(n.type==="worker"){
      ctx.strokeStyle="rgba(91,157,255,.10)"; ctx.beginPath(); ctx.moveTo(core.x,core.y); ctx.lineTo(n.x,n.y); ctx.stroke();
    }});

    // the LEAD core
    const t=performance.now()/1000;
    const cr=26+Math.sin(t*2)*3;
    const g=ctx.createRadialGradient(core.x,core.y,2,core.x,core.y,cr*2.4);
    g.addColorStop(0,"rgba(91,157,255,.9)"); g.addColorStop(.4,"rgba(91,157,255,.35)"); g.addColorStop(1,"rgba(91,157,255,0)");
    ctx.fillStyle=g; ctx.beginPath(); ctx.arc(core.x,core.y,cr*2.4,0,7); ctx.fill();
    ctx.fillStyle="#cfe0ff"; ctx.beginPath(); ctx.arc(core.x,core.y,cr*.55,0,7); ctx.fill();
    ctx.fillStyle="rgba(230,240,255,.9)"; ctx.font="700 11px Segoe UI,sans-serif"; ctx.textAlign="center";
    ctx.fillText("LEAD AI", core.x, core.y+cr*2.4+14);

    // nodes
    nodes.forEach(n=>{
      ctx.save();
      if(n.type==="worker"){
        ctx.shadowColor=n.color; ctx.shadowBlur=14;
        ctx.fillStyle=n.color; ctx.beginPath(); ctx.arc(n.x,n.y,n.r,0,7); ctx.fill();
        ctx.shadowBlur=0; ctx.fillStyle="#06101f"; ctx.font="800 11px Segoe UI"; ctx.textAlign="center"; ctx.textBaseline="middle";
        ctx.fillText((n.label||"?")[0].toUpperCase(), n.x, n.y);
        ctx.fillStyle="rgba(230,240,255,.72)"; ctx.font="600 10px Segoe UI"; ctx.textBaseline="top";
        ctx.fillText(trim(n.label,14), n.x, n.y+n.r+4);
      } else {
        const pulse = n.taskState==="assigned";
        const r = n.r + (pulse?Math.sin(t*5)*1.6:0);
        ctx.shadowColor=n.color; ctx.shadowBlur = n.taskState==="accepted"?16:9;
        ctx.fillStyle=n.color; ctx.globalAlpha = n.taskState==="queued"?.6:1;
        ctx.beginPath(); ctx.arc(n.x,n.y,r,0,7); ctx.fill(); ctx.globalAlpha=1;
        if(n.taskState==="accepted"){ ctx.strokeStyle="#06160f"; ctx.lineWidth=2; ctx.beginPath();
          ctx.moveTo(n.x-3,n.y); ctx.lineTo(n.x-1,n.y+2.5); ctx.lineTo(n.x+3.5,n.y-3); ctx.stroke(); }
      }
      ctx.restore();
    });

    // pulses travelling along edges
    for(let i=pulses.length-1;i>=0;i--){ const p=pulses[i]; p.t+=p.speed;
      const x=p.a.x+(p.b.x-p.a.x)*p.t, y=p.a.y+(p.b.y-p.a.y)*p.t;
      ctx.fillStyle=p.color; ctx.shadowColor=p.color; ctx.shadowBlur=12;
      ctx.beginPath(); ctx.arc(x,y,3.2,0,7); ctx.fill(); ctx.shadowBlur=0;
      if(p.t>=1) pulses.splice(i,1);
    }
    // bursts on review
    for(let i=bursts.length-1;i>=0;i--){ const b=bursts[i]; b.t+=0.05;
      ctx.strokeStyle=b.color; ctx.globalAlpha=1-b.t; ctx.lineWidth=2;
      ctx.beginPath(); ctx.arc(b.x,b.y,8+b.t*40,0,7); ctx.stroke(); ctx.globalAlpha=1;
      if(b.t>=1) bursts.splice(i,1);
    }
    // confetti on done
    if(confetti.length){ for(let i=confetti.length-1;i>=0;i--){ const c=confetti[i]; c.t+=0.016; c.vy+=0.12; c.x+=c.vx; c.y+=c.vy;
      ctx.fillStyle=c.c; ctx.globalAlpha=Math.max(0,1-c.t/2.2); ctx.fillRect(c.x,c.y,4,4); ctx.globalAlpha=1;
      if(c.t>2.2) confetti.splice(i,1); } }

    requestAnimationFrame(draw);
  }
  const trim=(s,n)=>{ s=String(s||""); return s.length>n?s.slice(0,n-1)+"…":s; };
  requestAnimationFrame(draw);
  // hitTest returns the raw task id under (x,y), or null.
  function hitTest(x,y){
    let best=null, bd=1e9;
    nodes.forEach(n=>{ if(n.type!=="task") return; const d=Math.hypot(n.x-x,n.y-y);
      if(d<Math.max(14,n.r+6) && d<bd){ bd=d; best=n.id.slice(2); } });
    return best;
  }
  return { sync, pulse, burst, celebrate, hitTest };
})();

// click a task node in the constellation to inspect it
$("#constellation").addEventListener("click",(e)=>{
  const id = sim.hitTest(e.clientX, e.clientY);
  if (id) openInspector(id);
});

connect();
})();
