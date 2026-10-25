"""
Reference worker poll-loop client (WORK_SPLIT.md §4.5). Stdlib only.

Each teammate runs this on THEIR laptop with THEIR key. The key stays local: it is
never sent to the leader's server — only results are.

  python worker/agent_client.py --server http://192.168.1.20:8000 --owner Alice --provider mock
  python worker/agent_client.py --server http://host:8000 --owner Bob --provider anthropic
      (key from --api-key or ANTHROPIC_API_KEY / OPENAI_API_KEY / KIMI_API_KEY)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lead.lead_ai import PROVIDERS  # noqa: E402
from worker.worker_agent import run_worker  # noqa: E402

HEARTBEAT_EVERY = 5.0


class ServerError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class ConsortiumClient:
    """Thin JSON client for the §4 worker endpoints."""

    def __init__(self, server: str, timeout: float = 30.0):
        self.server = server.rstrip("/")
        self.timeout = timeout
        self.worker_id: Optional[str] = None  # set by run_loop after register

    def _call(self, method: str, path: str, body: Optional[dict] = None,
              timeout: Optional[float] = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
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

    def register(self, owner, provider, model=None, room_code=None, worker_id=None) -> dict:
        body = {"owner": owner, "provider": provider}
        if model:
            body["model"] = model
        if room_code:
            body["room_code"] = room_code
        if worker_id:
            body["worker_id"] = worker_id
        return self._call("POST", "/api/worker/register", body)

    def heartbeat(self, wid) -> dict:
        return self._call("POST", f"/api/worker/{wid}/heartbeat", {})

    def next(self, wid, wait: int = 0) -> dict:
        path = f"/api/worker/{wid}/next" + (f"?wait={wait}" if wait else "")
        return self._call("POST", path, {}, timeout=self.timeout + wait)

    def submit(self, wid, task_id, result: dict) -> dict:
        return self._call("POST", f"/api/worker/{wid}/submit", {"task_id": task_id, **result})

    def tasks(self) -> list[dict]:
        return self._call("GET", "/api/tasks").get("tasks", [])

    def unregister(self, wid) -> dict:
        return self._call("POST", f"/api/worker/{wid}/unregister", {})


def dependency_outputs(client: ConsortiumClient, task: dict) -> list[tuple[str, str]]:
    """Fetch accepted results of the tasks this one depends on, so the AI can build on them."""
    deps = task.get("depends_on") or []
    if not deps:
        return []
    try:
        by_id = {t["id"]: t for t in client.tasks()}
    except Exception:
        return []
    return [(by_id[d].get("title", d), by_id[d].get("result", ""))
            for d in deps if d in by_id and by_id[d].get("result")]


def claim_dependency_outputs(client: ConsortiumClient, claim: dict) -> list[tuple[str, str]]:
    """Prefer dependency results bundled atomically with /next.

    Falling back to GET /api/tasks keeps this client compatible with older
    Consortium servers.
    """
    bundled = claim.get("dependency_outputs")
    if isinstance(bundled, list):
        return [(item.get("title", item.get("task_id", "dependency")),
                 item.get("output", ""))
                for item in bundled if isinstance(item, dict) and item.get("output")]
    return dependency_outputs(client, claim.get("task") or {})


class Heartbeat(threading.Thread):
    """Keeps last_seen fresh while a long AI call is running."""

    def __init__(self, client: ConsortiumClient, wid: str):
        super().__init__(daemon=True)
        self.client, self.wid = client, wid
        self.project_state = None
        self._stop_evt = threading.Event()

    def run(self):
        while not self._stop_evt.wait(HEARTBEAT_EVERY):
            try:
                self.project_state = self.client.heartbeat(self.wid).get("project_state")
            except Exception:
                pass  # flaky wifi: the main loop handles real outages

    def stop(self):
        self._stop_evt.set()


def run_loop(client: ConsortiumClient, owner: str, provider: str, api_key=None, model=None,
             room_code=None, poll: float = 2.0, long_poll: int = 0, max_tasks: int = 0,
             log=print, stop_event: Optional[threading.Event] = None,
             base_url=None, cli_cmd=None, worker_id=None) -> int:
    """Register, then claim -> execute -> submit until the project is done.

    Returns the number of tasks completed. `max_tasks`/`stop_event` exist for tests."""
    stop_event = stop_event or threading.Event()
    reg = client.register(owner, provider, model, room_code, worker_id)
    wid = client.worker_id = reg["worker"]["id"]
    log(f"registered as {reg['worker'].get('display', wid)} ({wid}); "
        f"project is {reg.get('project_state')}")
    hb = Heartbeat(client, wid)
    hb.start()
    done = 0
    backoff = poll
    try:
        while not stop_event.is_set():
            try:
                r = client.next(wid, long_poll)
            except ServerError as e:
                if e.status == 404:  # server forgot us (restart?) -> re-register
                    log("worker unknown to server; re-registering")
                    wid = client.worker_id = \
                        client.register(owner, provider, model, room_code, worker_id)["worker"]["id"]
                    hb.wid = wid
                    continue
                log(f"server error: {e}; retrying in {backoff:.0f}s")
                stop_event.wait(backoff)
                backoff = min(backoff * 2, 30)
                continue
            except (urllib.error.URLError, OSError) as e:
                log(f"cannot reach server ({e}); retrying in {backoff:.0f}s")
                stop_event.wait(backoff)
                backoff = min(backoff * 2, 30)
                continue
            backoff = poll

            if r.get("stop"):
                # /next says stop for both paused and done; only quit on done.
                try:
                    state = client.heartbeat(wid).get("project_state")
                except Exception:
                    state = None
                if state == "done":
                    log("project is done; stopping")
                    break
                stop_event.wait(poll)
                continue
            task = r.get("task")
            if not task:
                if not long_poll:
                    stop_event.wait(poll)
                continue

            log(f"-> task #{task.get('seq', '?')} {task.get('title', task['id'])}")
            result = run_worker(provider, task, r.get("project"),
                                claim_dependency_outputs(client, r), api_key, model,
                                base_url, cli_cmd)
            try:
                client.submit(wid, task["id"], result)
            except Exception as e:
                log(f"submit failed for {task['id']}: {e}")
                continue
            done += 1
            log(f"<- submitted {task['id']} ok={result['ok']} in {result['elapsed']}s"
                + ("" if result["ok"] else f" ({result['error'][:120]})"))
            if max_tasks and done >= max_tasks:
                break
    finally:
        hb.stop()
    return done


def main(argv=None):
    ap = argparse.ArgumentParser(description="Consortium worker: pull tasks, run your AI, submit.")
    ap.add_argument("--server", required=True, help="leader URL, e.g. http://192.168.1.20:8000")
    ap.add_argument("--owner", required=True, help="your name, e.g. Alice")
    ap.add_argument("--provider", required=True, choices=PROVIDERS)
    ap.add_argument("--api-key", default=None, help="your key (stays local); default: env var")
    ap.add_argument("--model", default=None)
    ap.add_argument("--base-url", default=None,
                    help="OpenAI-compatible endpoint for --provider custom "
                         "(e.g. https://openrouter.ai/api/v1)")
    ap.add_argument("--cli-cmd", default=None,
                    help="command for --provider cli — your own terminal agent. "
                         "Instruction is piped to stdin (or substituted for {prompt}); "
                         "stdout is the result. e.g. 'claude -p' or 'aider --message-file -'")
    ap.add_argument("--room-code", default=None)
    ap.add_argument("--worker-id", default=None,
                    help="adopt a worker slot the leader pre-created (from the "
                         "'Connect CLI' button) so tasks pinned to it reach you")
    ap.add_argument("--poll", type=float, default=2.0, help="seconds between polls when idle")
    ap.add_argument("--long-poll", type=int, default=0, help="ask server to hold /next up to N s")
    args = ap.parse_args(argv)

    if args.provider == "custom" and not (args.base_url or os.getenv("CUSTOM_BASE_URL")):
        ap.error("--provider custom requires --base-url (or CUSTOM_BASE_URL env)")
    if args.provider == "cli" and not (args.cli_cmd or os.getenv("CONSORTIUM_CLI_CMD")):
        ap.error("--provider cli requires --cli-cmd (or CONSORTIUM_CLI_CMD env), "
                 "e.g. --cli-cmd 'claude -p'")

    client = ConsortiumClient(args.server)

    def log(msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    try:
        n = run_loop(client, args.owner, args.provider, args.api_key, args.model,
                     args.room_code, args.poll, args.long_poll, log=log,
                     base_url=args.base_url, cli_cmd=args.cli_cmd, worker_id=args.worker_id)
        log(f"finished: {n} task(s) completed")
    except KeyboardInterrupt:
        log("interrupted; unregistering (in-flight task will be requeued)")
        if client.worker_id:
            try:
                client.unregister(client.worker_id)
            except Exception:
                pass
    except ServerError as e:
        log(f"could not register: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
