#!/usr/bin/env python3
"""
Consortium standalone worker — ONE self-contained file, stdlib only.

A teammate can drop just THIS file anywhere (even a blank folder) and run it.
No repo clone, no `cd agent-mesh`, no local imports. It pulls tasks the leader
assigns, runs them with the worker's chosen agent, and submits the result.

Examples:
  # Use your own CLI agent (no API key — the tool uses its own login):
  python consortium_worker.py --server https://host --owner Bob --provider cli --cli-cmd "claude -p"

  # Adopt a slot the leader pre-created via the "Connect CLI" button:
  python consortium_worker.py --server https://host --owner Bob --provider cli \
      --cli-cmd "claude -p" --worker-id bob-cli-1a2b

  # A model API (key stays on this machine, never sent to the host):
  python consortium_worker.py --server https://host --owner Ann --provider openai --api-key sk-...
  python consortium_worker.py --server https://host --owner Ann --provider custom \
      --base-url https://openrouter.ai/api/v1 --model meta-llama/llama-3.1-8b-instruct --api-key sk-...

  # No key, no CLI — just testing the flow:
  python consortium_worker.py --server https://host --owner Test --provider mock
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

PROVIDERS = ("anthropic", "openai", "kimi", "custom", "cli", "mock")
HEARTBEAT_EVERY = 5.0
LLM_TIMEOUT = float(os.getenv("LLM_TIMEOUT", "180"))
CLI_TIMEOUT = float(os.getenv("CONSORTIUM_CLI_TIMEOUT", "300"))
DEFAULT_MODELS = {"anthropic": "claude-sonnet-4-5", "openai": "gpt-4o",
                  "kimi": "moonshot-v1-8k", "custom": "gpt-3.5-turbo"}
PROVIDER_BASE = {"openai": "https://api.openai.com/v1",
                 "kimi": os.getenv("KIMI_BASE_URL", "https://api.moonshot.ai/v1")}
WORKER_SYSTEM = (
    "You are a WORKER AI on a team. The lead assigned you ONE task. Produce ONLY "
    "the deliverable the instruction asks for — no preamble, no explanation. Use "
    "exactly the shared names/ids the instruction specifies. If given outputs of "
    "earlier tasks, build on them."
)
_FENCE = re.compile(r"^```[\w+-]*[ \t]*\n(.*?)\n?```$", re.DOTALL)


# ───────────────────────── HTTP client ─────────────────────────
class ServerError(Exception):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class Client:
    def __init__(self, server, timeout=30.0):
        self.server = server.rstrip("/")
        self.timeout = timeout
        self.worker_id = None

    def call(self, method, path, body=None, timeout=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.server + path, data=data, method=method,
                                     headers={"content-type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            try:
                msg = json.loads(raw).get("error", raw)
            except ValueError:
                msg = raw
            raise ServerError(e.code, str(msg)[:300])

    def register(self, owner, provider, model=None, room_code=None, worker_id=None):
        body = {"owner": owner, "provider": provider}
        if model:
            body["model"] = model
        if room_code:
            body["room_code"] = room_code
        if worker_id:
            body["worker_id"] = worker_id
        return self.call("POST", "/api/worker/register", body)

    def heartbeat(self, wid):
        return self.call("POST", f"/api/worker/{wid}/heartbeat", {})

    def next(self, wid, wait=0):
        path = f"/api/worker/{wid}/next" + (f"?wait={wait}" if wait else "")
        return self.call("POST", path, {}, timeout=self.timeout + wait)

    def submit(self, wid, task_id, result):
        return self.call("POST", f"/api/worker/{wid}/submit", {"task_id": task_id, **result})

    def tasks(self):
        return self.call("GET", "/api/tasks").get("tasks", [])

    def unregister(self, wid):
        return self.call("POST", f"/api/worker/{wid}/unregister", {})


# ───────────────────────── execution ─────────────────────────
def strip_fence(text):
    m = _FENCE.match((text or "").strip())
    return m.group(1).strip() if m else (text or "")


def _post_llm(url, headers, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _build_prompt(project, title, instruction, dep_outputs):
    parts = [f"Project goal: {(project or {}).get('goal', '')}"]
    if (project or {}).get("summary"):
        parts.append(f"Progress so far: {project['summary']}")
    for dep_title, out in dep_outputs or []:
        parts.append(f'--- Output of earlier task "{dep_title}" ---\n{(out or "")[:8000]}')
    parts.append(f"=== YOUR TASK: {title} ===\n{instruction}")
    return "\n\n".join(parts)


def _run_cli(cmd, prompt):
    if not cmd:
        raise ValueError("cli provider needs --cli-cmd (e.g. 'claude -p')")
    if "{prompt}" in cmd:
        argv = shlex.split(cmd.replace("{prompt}", "'" + prompt.replace("'", "'\\''") + "'"),
                           posix=True)
        stdin_data = None
    else:
        argv = shlex.split(cmd, posix=True)
        stdin_data = prompt
    try:
        proc = subprocess.run(argv, input=stdin_data, capture_output=True,
                              text=True, timeout=CLI_TIMEOUT)
    except FileNotFoundError:
        raise ValueError(f"CLI command not found: {argv[0]!r}")
    except subprocess.TimeoutExpired:
        raise ValueError(f"CLI agent timed out after {CLI_TIMEOUT:.0f}s")
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 and not out:
        raise ValueError(f"CLI agent exited {proc.returncode}: {(proc.stderr or '')[:300]}")
    return out


def _mock(title, instruction):
    time.sleep(1.0)
    return f"[mock result] Completed: {title or instruction[:60]}"


def execute(provider, task, project, dep_outputs, api_key, model, base_url, cli_cmd):
    """-> dict matching POST /api/worker/{id}/submit minus task_id. Never raises."""
    start = time.time()
    title, instruction = task.get("title", ""), task.get("instruction", "")
    try:
        if provider == "mock":
            out = _mock(title, instruction)
        elif provider == "cli":
            out = strip_fence(_run_cli(cli_cmd or os.getenv("CONSORTIUM_CLI_CMD", ""),
                                       _build_prompt(project, title, instruction, dep_outputs)))
        else:
            key = api_key or os.getenv(
                {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY",
                 "kimi": "KIMI_API_KEY", "custom": "CUSTOM_API_KEY"}[provider])
            if not key:
                raise ValueError(f"no API key for {provider}")
            user = _build_prompt(project, title, instruction, dep_outputs)
            model = model or DEFAULT_MODELS[provider]
            if provider == "anthropic":
                data = _post_llm("https://api.anthropic.com/v1/messages",
                                 {"x-api-key": key, "anthropic-version": "2023-06-01",
                                  "content-type": "application/json"},
                                 {"model": model, "max_tokens": 4096, "system": WORKER_SYSTEM,
                                  "messages": [{"role": "user", "content": user}]})
                out = "".join(b.get("text", "") for b in data.get("content", [])
                              if b.get("type") == "text")
            else:
                base = base_url if provider == "custom" else PROVIDER_BASE[provider]
                if not base:
                    raise ValueError("custom provider needs --base-url")
                data = _post_llm(base.rstrip("/") + "/chat/completions",
                                 {"Authorization": f"Bearer {key}", "content-type": "application/json"},
                                 {"model": model, "temperature": 0.3,
                                  "messages": [{"role": "system", "content": WORKER_SYSTEM},
                                               {"role": "user", "content": user}]})
                out = data["choices"][0]["message"]["content"] or ""
            out = strip_fence(out)
        return {"ok": True, "output": out, "error": "", "elapsed": round(time.time() - start, 2)}
    except Exception as e:
        return {"ok": False, "output": "", "error": str(e)[:500],
                "elapsed": round(time.time() - start, 2)}


def dep_outputs(client, task):
    deps = task.get("depends_on") or []
    if not deps:
        return []
    try:
        by_id = {t["id"]: t for t in client.tasks()}
    except Exception:
        return []
    return [(by_id[d].get("title", d), by_id[d].get("result", ""))
            for d in deps if d in by_id and by_id[d].get("result")]


# ───────────────────────── loop ─────────────────────────
class Heartbeat(threading.Thread):
    def __init__(self, client, wid):
        super().__init__(daemon=True)
        self.client, self.wid, self._stop = client, wid, threading.Event()

    def run(self):
        while not self._stop.wait(HEARTBEAT_EVERY):
            try:
                self.client.heartbeat(self.wid)
            except Exception:
                pass

    def stop(self):
        self._stop.set()


def run_loop(client, args, log):
    reg = client.register(args.owner, args.provider, args.model, args.room_code, args.worker_id)
    wid = client.worker_id = reg["worker"]["id"]
    log(f"registered as {reg['worker'].get('display', wid)} ({wid}); project is {reg.get('project_state')}")
    hb = Heartbeat(client, wid); hb.start()
    done, backoff = 0, args.poll
    try:
        while True:
            try:
                r = client.next(wid, args.long_poll)
            except ServerError as e:
                if e.status == 404:
                    log("worker unknown to server; re-registering")
                    wid = client.worker_id = client.register(
                        args.owner, args.provider, args.model, args.room_code, args.worker_id)["worker"]["id"]
                    hb.wid = wid
                    continue
                log(f"server error: {e}; retry in {backoff:.0f}s"); time.sleep(backoff)
                backoff = min(backoff * 2, 30); continue
            except (urllib.error.URLError, OSError) as e:
                log(f"cannot reach server ({e}); retry in {backoff:.0f}s"); time.sleep(backoff)
                backoff = min(backoff * 2, 30); continue
            backoff = args.poll

            if r.get("stop"):
                try:
                    state = client.heartbeat(wid).get("project_state")
                except Exception:
                    state = None
                if state == "done":
                    log("project is done; stopping"); break
                time.sleep(args.poll); continue
            task = r.get("task")
            if not task:
                if not args.long_poll:
                    time.sleep(args.poll)
                continue

            log(f"-> task #{task.get('seq', '?')} {task.get('title', task['id'])}")
            result = execute(args.provider, task, r.get("project"), dep_outputs(client, task),
                             args.api_key, args.model, args.base_url, args.cli_cmd)
            try:
                client.submit(wid, task["id"], result)
            except Exception as e:
                log(f"submit failed for {task['id']}: {e}"); continue
            done += 1
            log(f"<- submitted {task['id']} ok={result['ok']} in {result['elapsed']}s"
                + ("" if result["ok"] else f" ({result['error'][:120]})"))
    finally:
        hb.stop()
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(description="Consortium standalone worker (single file).")
    ap.add_argument("--server", required=True, help="leader URL, e.g. https://host or http://ip:8000")
    ap.add_argument("--owner", required=True, help="your name")
    ap.add_argument("--provider", required=True, choices=PROVIDERS)
    ap.add_argument("--api-key", default=None, help="model-API key (stays local; never sent to host)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--base-url", default=None, help="OpenAI-compatible endpoint for --provider custom")
    ap.add_argument("--cli-cmd", default=None, help="command for --provider cli, e.g. 'claude -p'")
    ap.add_argument("--worker-id", default=None, help="adopt a slot from the 'Connect CLI' button")
    ap.add_argument("--room-code", default=None)
    ap.add_argument("--poll", type=float, default=2.0)
    ap.add_argument("--long-poll", type=int, default=0)
    args = ap.parse_args(argv)

    if args.provider == "custom" and not (args.base_url or os.getenv("CUSTOM_BASE_URL")):
        ap.error("--provider custom requires --base-url (or CUSTOM_BASE_URL)")
    if args.provider == "cli" and not (args.cli_cmd or os.getenv("CONSORTIUM_CLI_CMD")):
        ap.error("--provider cli requires --cli-cmd (or CONSORTIUM_CLI_CMD), e.g. --cli-cmd 'claude -p'")

    client = Client(args.server)

    def log(msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    try:
        n = run_loop(client, args, log)
        log(f"finished: {n} task(s) completed")
    except KeyboardInterrupt:
        log("interrupted; unregistering (in-flight task will be requeued)")
        if client.worker_id:
            try:
                client.unregister(client.worker_id)
            except Exception:
                pass
    except ServerError as e:
        log(f"could not register: {e}"); return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
