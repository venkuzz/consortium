'use strict';
const $ = id => document.getElementById(id);
const demo = new URLSearchParams(location.search).get('demo') === '1';
const state = { project: null, tasks: [], workers: [], filter: 'all', selected: null, busy: false, connected: demo, refreshing: false };
const symbols = { queued: '○', assigned: '◐', submitted: '◈', accepted: '✓', rejected: '×' };
const labels = { queued: 'Queued', assigned: 'In progress', submitted: 'Needs review', accepted: 'Accepted', rejected: 'Changes requested' };
const sessionKey = `consortium-worker:${demo ? 'demo' : location.origin}`;
let workerId = null, stream, toastTimer, sampleTimer, refreshAgain = false, automaticWorker = null, workerGeneration = 0;
try { workerId = demo ? null : sessionStorage.getItem(sessionKey); } catch {}
function setWorker(id) { workerId = id; try { id ? sessionStorage.setItem(sessionKey, id) : sessionStorage.removeItem(sessionKey); } catch {} }
function toast(message) { $('toast').textContent = message; $('toast').hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('toast').hidden = true, 6000); }
function log(message) { const li = document.createElement('li'), time = document.createElement('time'), text = document.createElement('span'); time.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }); time.dateTime = new Date().toISOString(); text.textContent = message; li.append(time, text); $('events').prepend(li); while ($('events').children.length > 35) $('events').lastChild.remove(); }
function element(tag, text, className) { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; if (className) el.className = className; return el; }
function connection(ok) { state.connected = ok; $('connection').textContent = demo ? 'Sample workspace' : ok ? 'Connected' : 'Reconnecting'; $('connection').className = `connection ${demo ? 'demo' : ok ? 'online' : ''}`; $('event-status').textContent = demo ? 'Local sample events' : ok ? 'Live from the host' : 'Waiting for the backend'; }
async function request(path, body) {
  if (demo) return demoRequest(path, body);
  const res = await fetch(path, { ...(body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }), cache: 'no-store', signal: AbortSignal.timeout(path.endsWith('/plan') ? 180000 : 15000) });
  if (!res.ok) { let message; try { message = (await res.json()).error; } catch {} throw new Error(message || `The server returned ${res.status}. Check the backend connection.`); }
  return res.json();
}
async function sync() {
  if (demo) { render(); return; }
  if (state.refreshing) { refreshAgain = true; return; }
  state.refreshing = true;
  try { const [p, t, w] = await Promise.all([request('/api/project'), request('/api/tasks'), request('/api/workers')]); state.project = p.project; state.tasks = t.tasks; state.workers = w.workers; connection(true); render(); }
  catch (error) { connection(false); render(); }
  finally { state.refreshing = false; if (refreshAgain) { refreshAgain = false; sync(); } }
}
async function mutate(path, body, success) {
  if (state.busy) return null;
  state.busy = true; renderControls();
  try { const response = await request(path, body); await sync(); if (success) log(success); return response; }
  catch (error) { toast(error.message); throw error; }
  finally { state.busy = false; renderControls(); }
}
function selectedTask() { return state.tasks.find(t => t.id === state.selected); }
function renderControls() {
  const unavailable = state.busy || !state.connected, project = state.project;
  for (const id of ['create', 'join', 'plan', 'pause', 'done', 'add-task', 'claim', 'leave', 'new-project', 'sample-run']) $(id).disabled = unavailable;
  $('create').disabled = unavailable || !$('goal').value.trim();
  $('plan').textContent = state.busy ? 'Working…' : 'Plan tasks';
  $('add-task').disabled = unavailable || !project || project.state === 'done';
  $('claim').disabled = unavailable || !!automaticWorker || project?.state !== 'active' || state.tasks.some(t => t.assigned_to === workerId && t.state === 'assigned');
  $('claim').textContent = automaticWorker ? 'Automatic worker active' : 'Claim next task';
  $('sample-run').disabled = unavailable || project?.state !== 'active' || !state.tasks.some(t => t.state === 'queued');
  $('new-project').hidden = !project; $('pause').hidden = !project || !['active', 'paused'].includes(project.state); $('done').hidden = !project || project.state === 'done';
  $('pause').textContent = project?.state === 'paused' ? 'Resume' : 'Pause';
  document.querySelectorAll('dialog button[type="submit"], #review-form button, #submit-form button').forEach(b => b.disabled = unavailable);
}
function render() {
  const p = state.project;
  const projectLabel = p ? ({ planning: 'Planning', active: 'Project active', paused: 'Project paused', done: 'Project complete' }[p.state] || p.state) : 'No project';
  $('project-state').textContent = projectLabel;
  $('overview-state').textContent = p ? ({ planning: 'Planning', active: 'Active', paused: 'Paused', done: 'Complete' }[p.state] || p.state) : 'Not started';
  $('project-title').textContent = p?.goal || 'Give the team a direction.';
  $('project-summary').textContent = p?.summary || (p ? 'The lead sets the direction. Workers claim tasks and return results for review.' : 'Set a goal. Your lead AI will break it into work the team can share.');
  $('goal-section').hidden = Boolean(p); $('plan-bar').hidden = p?.state !== 'planning';
  const accepted = state.tasks.filter(t => t.state === 'accepted').length;
  const submitted = state.tasks.filter(t => t.state === 'submitted').length;
  $('progress-text').textContent = state.tasks.length ? `${accepted} of ${state.tasks.length} tasks accepted` : 'No tasks yet'; $('progress-fill').style.width = `${state.tasks.length ? accepted / state.tasks.length * 100 : 0}%`;
  $('overview-tasks').textContent = state.tasks.length; $('overview-review').textContent = submitted; $('overview-fill').style.width = `${state.tasks.length ? accepted / state.tasks.length * 100 : 0}%`;
  $('task-count').textContent = state.tasks.length; $('review-count').textContent = submitted;
  renderWorkers(); renderTasks(); renderDetail(); renderControls();
}
function renderWorkers() {
  $('workers').replaceChildren(); $('worker-count').textContent = state.workers.length; $('worker-empty').hidden = !!state.workers.length;
  for (const w of state.workers) { const row = element('div', undefined, 'worker-row'), avatar = element('span', w.display.slice(0, 1).toUpperCase(), 'avatar'), text = element('div'); text.append(element('strong', w.display), element('small', `${w.provider} / ${w.status}`)); row.append(avatar, text, element('span', '', `worker-state ${w.status}`)); $('workers').append(row); }
  const me = state.workers.find(w => w.id === workerId); $('worker-console').hidden = !me; $('my-worker').textContent = me ? `${me.display} (${me.status})` : '';
  $('worker-help').textContent = automaticWorker ? 'This tab claims eligible tasks, calls your selected provider, and submits the result automatically.' : 'Use your AI to complete the instruction, then submit the result.';
  $('join').hidden = !!me;
}
function renderTasks() {
  const tasks = state.tasks.filter(t => state.filter === 'all' || t.state === state.filter).sort((a, b) => a.seq - b.seq);
  $('task-list').replaceChildren(); $('queue-empty').hidden = !!tasks.length;
  $('queue-empty').querySelector('h3').textContent = state.tasks.length ? 'No tasks in this view.' : 'The work starts with a plan.';
  $('queue-empty').querySelector('p').textContent = state.tasks.length ? 'Choose another filter to see the rest of the work.' : 'Create a project, then ask the lead AI to plan the tasks.';
  for (const task of tasks) {
    const row = element('button', undefined, `task-row ${task.id === state.selected ? 'selected' : ''}`); row.setAttribute('aria-pressed', String(task.id === state.selected));
    const copy = element('span', undefined, 'task-copy'); copy.append(element('strong', task.title));
    const worker = state.workers.find(w => w.id === (task.assigned_to || task.assignee));
    const pinNote = task.assignee && !task.assigned_to && worker ? ` / → ${worker.display}` : worker ? ` / ${worker.display}` : '';
    copy.append(element('small', `${labels[task.state] || task.state}${pinNote}`));
    row.append(element('span', symbols[task.state] || '○', `task-symbol ${task.state}`), copy, element('span', `#${String(task.seq).padStart(2, '0')}`, 'task-number'));
    row.addEventListener('click', () => { if (state.selected !== task.id) { $('review-note').value = ''; $('followup').value = ''; $('worker-output').value = ''; $('output-frame').hidden = true; $('output-frame').srcdoc = ''; } state.selected = task.id; renderTasks(); renderDetail(); }); $('task-list').append(row);
  }
}
function renderDetail() {
  const task = selectedTask(); $('detail-empty').hidden = !!task; $('task-detail').hidden = !task; if (!task) return;
  $('detail-state').textContent = labels[task.state] || task.state; $('detail-state').className = `state-chip ${task.state}`; $('detail-seq').textContent = `Task ${task.seq}`;
  $('detail-title').textContent = task.title; $('detail-instruction').textContent = task.instruction;
  const pinned = task.assignee ? state.workers.find(w => w.id === task.assignee) : null;
  const claimed = state.workers.find(w => w.id === task.assigned_to);
  $('detail-worker').textContent = claimed ? claimed.display
    : pinned ? `Assigned to ${pinned.display} (waiting to start)`
    : 'Available to any worker';
  // Leader can (re)assign a task that hasn't been claimed yet.
  const assignable = ['queued', 'rejected'].includes(task.state) && state.workers.length && state.project && !['done'].includes(state.project.state);
  $('assign-row').hidden = !assignable;
  if (assignable) {
    const sel = $('assign-select');
    sel.replaceChildren();
    sel.append(element('option', 'Anyone (open)', ''));
    sel.lastChild.value = '';
    for (const w of state.workers) { const o = element('option', `${w.display} (${w.status})`); o.value = w.id; sel.append(o); }
    sel.value = task.assignee || '';
  }
  $('detail-deps').textContent = task.depends_on?.length ? `Depends on: ${task.depends_on.map(id => state.tasks.find(t => t.id === id)?.title || id).join(', ')}` : '';
  // Worker one-click: "Take this task" pins it to ME so my connected client
  // (browser automatic worker or a running agent_client.py) runs it next.
  const me = state.workers.find(w => w.id === workerId);
  const takeable = me && ['queued', 'rejected'].includes(task.state) && task.assignee !== workerId && state.project?.state === 'active';
  $('take-task').hidden = !takeable;
  $('output-section').hidden = !task.result; $('detail-output').textContent = task.result || ''; $('preview-output').hidden = !/<[a-z][\s\S]*>/i.test(task.result || '');
  $('review-note-section').hidden = !task.review_note; $('detail-review').textContent = task.review_note || '';
  $('review-form').hidden = task.state !== 'submitted' || state.project?.state === 'done';
  $('submit-form').hidden = task.state !== 'assigned' || task.assigned_to !== workerId || state.project?.state === 'done';
}
$('goal').addEventListener('input', renderControls);
$('create').addEventListener('click', async () => { try { await mutate('/api/project', { goal: $('goal').value.trim() }, 'Project created.'); } catch {} });
$('plan').addEventListener('click', async () => { try { const data = await mutate('/api/project/plan', {}, 'Lead AI planned the work.'); if (data?.note) log(data.note); } catch {} });
$('pause').addEventListener('click', async () => { const next = state.project?.state === 'paused' ? 'active' : 'paused'; try { await mutate('/api/project/state', { state: next }, `Project ${next === 'active' ? 'resumed' : 'paused'}.`); } catch {} });
$('done').addEventListener('click', async () => { if (!confirm('Mark this project done? Workers will stop claiming tasks. This cannot be resumed.')) return; try { await mutate('/api/project/state', { state: 'done' }, 'Project marked done.'); } catch {} });
$('new-project').addEventListener('click', () => { if (!confirm('Start a new project? Creating it will replace the current project and clear its tasks.')) return; $('goal-section').hidden = false; $('goal').value = ''; $('goal').focus(); $('goal-help').textContent = 'Creating this project will clear the previous task list.'; renderControls(); });
document.querySelectorAll('[data-filter]').forEach(button => button.addEventListener('click', () => { state.filter = button.dataset.filter; document.querySelectorAll('[data-filter]').forEach(b => b.setAttribute('aria-pressed', String(b === button))); renderTasks(); }));
document.querySelectorAll('[data-close]').forEach(b => b.addEventListener('click', () => $(b.dataset.close).close()));
$('join').addEventListener('click', () => { $('join-error').textContent = ''; $('join-dialog').showModal(); $('owner').focus(); });
function updateProviderFields() {
  const provider = demo ? 'mock' : $('provider').value;
  $('credentials').hidden = provider === 'mock' || provider === 'cli';
  $('custom-endpoint').hidden = provider !== 'custom';
  $('provider-help').hidden = provider !== 'cli';
  $('provider-help').textContent = provider === 'cli' ? 'CLI agents normally connect through worker/agent_client.py and run on the teammate’s computer. A browser registration remains in manual mode.' : '';
  if (provider !== 'custom') $('base-url').value = '';
  $('key').value = '';
}
$('provider').addEventListener('change', updateProviderFields);
$('join-dialog').addEventListener('close', () => { $('key').value = ''; });
$('join-form').addEventListener('submit', async event => {
  event.preventDefault(); if (!$('owner').value.trim()) return;
  $('join-error').textContent = '';
  const provider = demo ? 'mock' : $('provider').value;
  const config = { provider, key: demo ? '' : $('key').value.trim(), baseUrl: demo ? '' : $('base-url').value.trim(), model: demo ? '' : $('model').value.trim() };
  if (provider === 'custom' && config.key && !config.baseUrl) { $('join-error').textContent = 'Add the OpenAI-compatible base URL to run this worker automatically.'; return; }
  try {
    const data = await mutate('/api/worker/register', { owner: $('owner').value.trim(), provider, api_key: '', model: config.model, room_code: $('room-code').value.trim() });
    if (!data) return;
    setWorker(data.worker.id); $('join-dialog').close(); $('key').value = '';
    // Live backend blocks mock workers from claiming (placeholder output only),
    // so only auto-run mock in the offline sample workspace. Real auto work
    // needs a key; otherwise it's manual / CLI.
    if ((provider === 'mock' && demo) || (provider !== 'mock' && config.key)) startAutomaticWorker(data.worker.id, config);
    render();
    if (provider === 'mock' && !demo) {
      log(`${data.worker.display} joined as a Mock worker — Mock can't do real tasks on the live server. Use a CLI agent or a provider key.`);
      toast('Mock workers are test-only and can\u2019t claim tasks here. Join with a CLI agent or a provider key to do real work.');
    } else {
      log(`${data.worker.display} joined${automaticWorker ? ' in automatic mode' : ' in manual mode'}.`);
    }
  }
  catch (error) { $('join-error').textContent = error.message; }
});
$('leave').addEventListener('click', async () => { const leavingWorker = workerId; stopAutomaticWorker(); try { await mutate(`/api/worker/${encodeURIComponent(leavingWorker)}/unregister`, {}, 'You left the worker team.'); setWorker(null); render(); } catch {} });
$('claim').addEventListener('click', async () => { try { const data = await mutate(`/api/worker/${encodeURIComponent(workerId)}/next`, {}); if (data?.task) { state.selected = data.task.id; $('worker-output').value = ''; render(); log(`Claimed: ${data.task.title}`); } else toast(data?.stop ? 'The project is paused or done.' : 'No eligible tasks yet. Waiting for reviews or new tasks.'); } catch {} });
$('submit-form').addEventListener('submit', async event => { event.preventDefault(); const task = selectedTask(); if (!task || !$('worker-output').value.trim()) return; try { await mutate(`/api/worker/${encodeURIComponent(workerId)}/submit`, { task_id: task.id, output: $('worker-output').value, ok: true, error: '', elapsed: 0 }, `Submitted: ${task.title}`); $('worker-output').value = ''; } catch {} });
$('review-form').addEventListener('submit', async event => { event.preventDefault(); const task = selectedTask(), decision = event.submitter?.value; if (!task || !['accepted', 'rejected'].includes(decision)) return; try { await mutate(`/api/tasks/${encodeURIComponent(task.id)}/review`, { decision, note: $('review-note').value.trim(), ...(decision === 'rejected' && $('followup').value.trim() ? { followup_instruction: $('followup').value.trim() } : {}) }, `${task.title}: ${decision}.`); $('review-note').value = ''; $('followup').value = ''; } catch {} });
$('add-task').addEventListener('click', () => { $('dependencies').replaceChildren(); state.tasks.forEach(t => { const opt = element('option', t.title); opt.value = t.id; $('dependencies').append(opt); }); const asg = $('task-assignee'); asg.replaceChildren(); const anyone = element('option', 'Anyone (open)'); anyone.value = ''; asg.append(anyone); state.workers.forEach(w => { const o = element('option', `${w.display} (${w.status})`); o.value = w.id; asg.append(o); }); $('task-error').textContent = ''; $('task-dialog').showModal(); });
$('task-form').addEventListener('submit', async event => { event.preventDefault(); const title = $('task-title').value.trim(), instruction = $('task-instruction').value.trim(); if (!title || !instruction) return; try { const data = await mutate('/api/tasks', { title, instruction, depends_on: [...$('dependencies').selectedOptions].map(o => o.value), assignee: $('task-assignee').value || '' }, `Task added: ${title}`); if (data) { $('task-dialog').close(); $('task-form').reset(); state.selected = data.task.id; render(); } } catch (error) { $('task-error').textContent = error.message; } });
async function copy(text) { try { await navigator.clipboard.writeText(text); toast('Copied.'); } catch { toast('Clipboard unavailable. Select the text and copy it manually.'); } }
$('copy-instruction').addEventListener('click', () => copy(selectedTask()?.instruction || ''));
$('assign-btn').addEventListener('click', async () => {
  const task = selectedTask(); if (!task) return;
  const worker_id = $('assign-select').value;
  const name = worker_id ? (state.workers.find(w => w.id === worker_id)?.display || 'worker') : 'anyone';
  try { await mutate(`/api/tasks/${encodeURIComponent(task.id)}/assign`, { worker_id }, `${task.title}: assigned to ${name}.`); }
  catch {}
});
function shellQuote(s) { return `"${String(s).replace(/(["\\$`])/g, '\\$1')}"`; }
async function workerServerAddress() {
  if (!['localhost', '127.0.0.1', '[::1]'].includes(location.hostname)) return location.origin;
  try { return (await request('/api/health')).lan_url || location.origin; }
  catch { return location.origin; }
}
// One pasted command: download the single-file worker from THIS server, then
// boot it so the teammate's CLI agent starts pulling and submitting tasks.
// No repo clone, no separate download step.
function cliCommandFor(server, workerId, owner, cliCmd, os, roomCode = '') {
  const dl = server.replace(/\/$/, '') + '/worker';
  const common = `--server ${shellQuote(server)} --owner ${shellQuote(owner)} ` +
    `--provider cli --cli-cmd ${shellQuote(cliCmd)} --worker-id ${shellQuote(workerId)} ` +
    `--long-poll 20` + (roomCode ? ` --room-code ${shellQuote(roomCode)}` : '');
  if (os === 'bash') {
    // macOS / Linux: fetch to a temp file and exec it.
    return `curl -fsSL ${shellQuote(dl)} -o /tmp/consortium_worker.py && ` +
      `python3 /tmp/consortium_worker.py ${common}`;
  }
  // Windows PowerShell: download then run, as one line.
  return `curl.exe -fsSL ${shellQuote(dl)} -o $env:TEMP\\consortium_worker.py; ` +
    `python $env:TEMP\\consortium_worker.py ${common}`;
}
$('connect-cli-btn').addEventListener('click', () => {
  const task = selectedTask(); if (!task) return;
  if (demo) { toast('Connect CLI needs the live backend, not the sample workspace.'); return; }
  $('cli-error').textContent = ''; $('cli-status').textContent = '';
  $('cli-command-box').hidden = true; $('cli-generated').value = '';
  // Prefill name from an existing CLI worker pinned to this task, if any.
  const pinned = state.workers.find(w => w.id === task.assignee);
  $('cli-owner').value = pinned?.owner || pinned?.display || '';
  $('cli-dialog').showModal(); $('cli-owner').focus();
});
$('cli-form').addEventListener('submit', async event => {
  event.preventDefault();
  const task = selectedTask(); if (!task) return;
  const owner = $('cli-owner').value.trim(); const cliCmd = $('cli-command').value.trim() || 'claude -p';
  if (!owner) { $('cli-error').textContent = 'Enter the teammate\u2019s name.'; return; }
  $('cli-error').textContent = ''; $('cli-status').textContent = 'Reserving a worker slot\u2026';
  try {
    // 1. Pre-create a CLI worker slot the teammate's client will adopt.
    const reg = await request('/api/worker/register', { owner, provider: 'cli', room_code: $('room-code').value.trim() });
    const wid = reg.worker.id;
    // 2. Pin this task to that slot so the CLI pulls exactly what you assigned.
    await request(`/api/tasks/${encodeURIComponent(task.id)}/assign`, { worker_id: wid });
    await sync();
    // 3. Show the one-paste command for the teammate to run (both OSes).
    const server = await workerServerAddress();
    const roomCode = $('room-code').value.trim();
    const winCmd = cliCommandFor(server, wid, owner, cliCmd, 'powershell', roomCode);
    const nixCmd = cliCommandFor(server, wid, owner, cliCmd, 'bash', roomCode);
    $('cli-generated').value =
      `# Windows (PowerShell):\n${winCmd}\n\n# macOS / Linux:\n${nixCmd}`;
    $('cli-command-box').hidden = false;
    $('cli-status').textContent = `Task pinned to ${reg.worker.display}. Have them open their project checkout or worktree, then paste the line for their OS. It downloads the worker and boots their CLI in that directory.`;
    log(`Reserved CLI worker for ${owner} and pinned "${task.title}".`);
  } catch (error) { $('cli-status').textContent = ''; $('cli-error').textContent = error.message; }
});
$('cli-copy').addEventListener('click', () => copy($('cli-generated').value));
$('take-task').addEventListener('click', async () => {
  const task = selectedTask(); if (!task || !workerId) return;
  try {
    await mutate(`/api/tasks/${encodeURIComponent(task.id)}/assign`, { worker_id: workerId }, `You took "${task.title}". Your AI will run it next.`);
    if (!automaticWorker) toast('Task is yours. Click "Claim next task" or run your worker client to start it.');
  } catch {}
});
$('preview-output').addEventListener('click', () => { const frame = $('output-frame'); frame.hidden = !frame.hidden; if (!frame.hidden) frame.srcdoc = selectedTask()?.result || ''; });
$('invite').addEventListener('click', async () => { $('invite-address').value = await workerServerAddress(); $('invite-help').textContent = 'Share this backend address with teammates. They must be on the same network unless you are using a public tunnel.'; $('invite-dialog').showModal(); });
$('copy-address').addEventListener('click', () => copy($('invite-address').value));
function connectStream() {
  stream = new EventSource('/api/events');
  stream.addEventListener('snapshot', event => { try { const data = JSON.parse(event.data); state.project = data.project; state.tasks = data.tasks || []; state.workers = data.workers || []; connection(true); render(); } catch { connection(false); } });
  const descriptions = { project_state: 'Project state changed.', tasks_planned: 'Lead AI planned tasks.', task_created: 'New task added.', task_claimed: 'A worker claimed a task.', task_submitted: 'A result is ready for review.', task_reviewed: 'Task review updated.', worker_joined: 'A worker joined.', worker_offline: 'A worker went offline; its task was requeued.', worker_left: 'A worker left.', done: 'Project complete.' };
  for (const [name, description] of Object.entries(descriptions)) stream.addEventListener(name, () => { log(description); sync(); });
  stream.onerror = () => { connection(false); renderControls(); }; // EventSource reconnects and replays a fresh snapshot.
}
async function heartbeat() { if (!workerId || demo) return; try { await request(`/api/worker/${encodeURIComponent(workerId)}/heartbeat`, {}); } catch {} }
const PROVIDER_BASE = { openai: 'https://api.openai.com/v1', kimi: 'https://api.moonshot.ai/v1' };
const DEFAULT_MODEL = { anthropic: 'claude-sonnet-4-5', openai: 'gpt-4o', kimi: 'moonshot-v1-8k', custom: 'gpt-3.5-turbo' };
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
function stopAutomaticWorker() { workerGeneration += 1; automaticWorker = null; renderControls(); }
function startAutomaticWorker(id, config) {
  const generation = ++workerGeneration;
  automaticWorker = { id, ...config };
  renderControls();
  runAutomaticWorker(generation, automaticWorker);
}
async function runAutomaticWorker(generation, config) {
  while (generation === workerGeneration && automaticWorker && workerId === config.id) {
    if (state.project?.state !== 'active') { await sleep(1400); continue; }
    try {
      const claim = await request(`/api/worker/${encodeURIComponent(config.id)}/next`, {});
      if (claim.stop) break;
      if (!claim.task) { await sleep(1800); continue; }
      state.selected = claim.task.id; await sync(); render();
      log(`Automatic worker claimed: ${claim.task.title}`);
      const started = performance.now();
      let output = '', ok = true, error = '';
      try { output = await executeProviderTask(config, claim.task, claim.project || state.project, claim.dependency_outputs || []); }
      catch (providerError) { ok = false; error = String(providerError.message || providerError).slice(0, 500); output = `[worker error from ${config.provider}] ${error}`; }
      if (generation !== workerGeneration || !automaticWorker) break;
      await request(`/api/worker/${encodeURIComponent(config.id)}/submit`, { task_id: claim.task.id, output, ok, error, elapsed: +((performance.now() - started) / 1000).toFixed(2) });
      await sync(); log(`${claim.task.title}: ${ok ? 'submitted automatically' : 'provider error submitted for review'}.`);
    } catch (error) {
      if (generation !== workerGeneration) break;
      log(`Automatic worker paused after an error: ${error.message}`);
      await sleep(3000);
    }
  }
  if (generation === workerGeneration) { automaticWorker = null; renderControls(); }
}
async function executeProviderTask(config, task, project, dependencyOutputs = []) {
  if (config.provider === 'mock') {
    await sleep(900 + Math.random() * 900);
    return `<!-- ${task.title} · generated by the browser mock worker -->\n<section data-task="${task.id}">\n  <p>Deliverable for: ${project?.goal || 'Consortium project'}</p>\n  <p>${task.instruction.slice(0, 240)}</p>\n</section>`;
  }
  const system = 'You are a worker AI on a collaborative build team. Produce only the deliverable requested by the instruction, without a preamble.';
  const dependencies = dependencyOutputs.map(dep => `\n--- Output of dependency "${dep.title || dep.task_id}" ---\n${dep.output || ''}`).join('');
  const user = `Project goal: ${project?.goal || ''}${project?.summary ? `\nProgress so far: ${project.summary}` : ''}${dependencies}\nTask: ${task.title}\nInstruction:\n${task.instruction}`;
  if (config.provider === 'anthropic') {
    const response = await fetch('https://api.anthropic.com/v1/messages', { method: 'POST', headers: { 'x-api-key': config.key, 'anthropic-version': '2023-06-01', 'content-type': 'application/json', 'anthropic-dangerous-direct-browser-access': 'true' }, body: JSON.stringify({ model: config.model || DEFAULT_MODEL.anthropic, max_tokens: 4096, system, messages: [{ role: 'user', content: user }] }), signal: AbortSignal.timeout(180000) });
    if (!response.ok) throw new Error(`Anthropic returned ${response.status}: ${(await response.text()).slice(0, 180)}`);
    const data = await response.json();
    return (data.content || []).filter(block => block.type === 'text').map(block => block.text).join('').trim() || '[empty response]';
  }
  const base = config.provider === 'custom' ? config.baseUrl : PROVIDER_BASE[config.provider];
  if (!base) throw new Error('No API base URL is configured for this provider.');
  const response = await fetch(base.replace(/\/$/, '') + '/chat/completions', { method: 'POST', headers: { Authorization: `Bearer ${config.key}`, 'content-type': 'application/json' }, body: JSON.stringify({ model: config.model || DEFAULT_MODEL[config.provider], temperature: .3, messages: [{ role: 'system', content: system }, { role: 'user', content: user }] }), signal: AbortSignal.timeout(180000) });
  if (!response.ok) throw new Error(`Provider returned ${response.status}: ${(await response.text()).slice(0, 180)}`);
  const data = await response.json();
  return (data.choices?.[0]?.message?.content || '').trim() || '[empty response]';
}
function newTask(title, instruction, depends_on = [], assignee = null) { return { id: `t_${crypto.randomUUID()}`, seq: state.tasks.length + 1, project_id: state.project.id, title, instruction, state: 'queued', assigned_to: null, assignee, depends_on, result: '', review_note: '' }; }
function sampleWorker(owner) { return { id: `w_${crypto.randomUUID()}`, owner, provider: 'mock', display: `${owner}'s Mock`, status: 'idle', last_seen: Date.now() / 1000 }; }
async function demoRequest(path, body) {
  if (path === '/api/project' && body) { clearTimeout(sampleTimer); state.project = { id: 'sample-project', goal: body.goal, state: 'planning', summary: '' }; state.tasks = []; state.selected = null; state.workers.forEach(w => w.status = 'idle'); return { project: state.project }; }
  if (path === '/api/project/plan') { state.tasks = []; for (const [title, instruction] of [['Define the page structure', 'Create semantic HTML for a team-matching page with a hero, a team list, and a join form. Use #hero, #teams, and #join.'], ['Design the visual system', 'Provide CSS for #hero, #teams, and #join. Use a blue and white palette, readable typography, and a responsive layout.'], ['Check the complete experience', 'Check keyboard navigation, form labels, mobile layout, and readable contrast. Report any missing requirements.']]) state.tasks.push(newTask(title, instruction)); state.project.state = 'active'; return { project: state.project, tasks: state.tasks, used_llm: false, note: 'Sample tasks only. No model was called.' }; }
  if (path === '/api/project/state') { state.project.state = body.state; return { project: state.project }; }
  if (path === '/api/tasks') { const task = newTask(body.title, body.instruction, body.depends_on, body.assignee || null); state.tasks.push(task); return { task }; }
  if (path.endsWith('/assign')) { const task = state.tasks.find(t => path.includes(t.id)); if (task) { task.assignee = body.worker_id || null; } return { task }; }
  if (path.endsWith('/review')) { const task = state.tasks.find(t => path.includes(t.id)); task.state = body.decision; task.review_note = body.note; let followup = null; if (body.decision === 'rejected' && body.followup_instruction) { followup = newTask(`Revise: ${task.title}`, body.followup_instruction); state.tasks.push(followup); } return { task, followup }; }
  if (path === '/api/worker/register') { const worker = sampleWorker(body.owner); state.workers.push(worker); return { worker }; }
  const worker = state.workers.find(w => path.includes(w.id));
  if (path.endsWith('/unregister')) { state.workers = state.workers.filter(w => w !== worker); state.tasks.filter(t => t.assigned_to === worker?.id && t.state === 'assigned').forEach(t => { t.state = 'queued'; t.assigned_to = null; }); return { ok: true }; }
  if (path.endsWith('/next')) { if (state.project?.state !== 'active') return { task: null, stop: true }; const eligible = state.tasks.filter(t => t.state === 'queued' && (!t.assignee || t.assignee === worker.id) && t.depends_on.every(id => state.tasks.find(d => d.id === id)?.state === 'accepted')); eligible.sort((a, b) => ((a.assignee === worker.id ? 0 : 1) - (b.assignee === worker.id ? 0 : 1)) || a.seq - b.seq); const task = eligible[0]; if (task) { task.state = 'assigned'; task.assigned_to = worker.id; worker.status = 'working'; } return { task: task || null }; }
  if (path.endsWith('/submit')) { const task = state.tasks.find(t => t.id === body.task_id); task.result = body.output; task.state = 'submitted'; worker.status = 'idle'; return { ok: true }; }
  throw new Error('Unsupported sample action.');
}
$('sample-run').addEventListener('click', () => {
  if (state.project?.state !== 'active') return;
  if (!state.workers.some(w => w.id !== workerId)) state.workers.push(sampleWorker('Sample worker'));
  const w = state.workers.find(w => w.id !== workerId);
  const tasks = state.tasks.filter(t => t.state === 'queued' && t.depends_on.every(id => state.tasks.find(d => d.id === id)?.state === 'accepted'));
  if (!tasks.length) return toast('These tasks are waiting for their dependencies to be accepted.');
  tasks.forEach(t => { t.state = 'assigned'; t.assigned_to = w.id; }); w.status = 'working'; render(); log('Sample worker claimed the eligible tasks.');
  sampleTimer = setTimeout(() => { if (state.project?.state === 'done') return; tasks.filter(t => state.tasks.includes(t) && t.state === 'assigned').forEach(t => { t.state = 'submitted'; t.result = `Sample result for: ${t.title}\n\nThis is illustrative output for trying the review workflow. Connect the backend and a worker client to produce real work.`; }); w.status = 'idle'; render(); log('Sample results are ready for your review.'); }, 1200);
});
$('demo-banner').hidden = !demo;
if (demo) { $('provider').value = 'mock'; $('provider').disabled = true; $('goal').value = 'Build a team-matching page for our hackathon.'; connection(true); log('Sample workspace ready. Create a project to begin.'); render(); }
else { render(); sync(); connectStream(); setInterval(heartbeat, 5000); }
updateProviderFields();
window.addEventListener('pagehide', () => { stopAutomaticWorker(); stream?.close(); });
