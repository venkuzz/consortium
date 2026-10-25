"""Regression coverage for worker/frontend integration failures."""
from __future__ import annotations

import json
import os
import sys
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.state import ACCEPTED, ACTIVE, QUEUED, REJECTED  # noqa: E402
from core.store import Store, WORKER_TIMEOUT  # noqa: E402
from server import app  # noqa: E402
from server import routes_project as rp  # noqa: E402
from worker.agent_client import claim_dependency_outputs  # noqa: E402


class WorkflowRegressionTests(unittest.TestCase):
    def active_store(self, events=None):
        store = Store(broadcast=(lambda name, data: events.append((name, data)))
                      if events is not None else None)
        store.create_project("regression test")
        store.set_state(ACTIVE)
        return store

    def test_assigning_rejected_task_requeues_clean_attempt(self):
        store = self.active_store()
        worker = store.register_worker("Alice", "cli").id
        task = store.add_task("Retry me", "do it")
        store.claim_next(worker)
        store.submit(worker, task.id, "bad output")
        store.review(task.id, REJECTED, "incorrect")

        retried = store.assign_task(task.id, worker)
        self.assertEqual(retried.state, QUEUED)
        self.assertEqual(retried.assignee, worker)
        self.assertEqual(retried.result, "")
        self.assertEqual(retried.review_note, "")
        self.assertEqual(store.claim_next(worker)["task"]["id"], task.id)

    def test_only_submitted_tasks_can_be_reviewed(self):
        store = self.active_store()
        task = store.add_task("Queued", "not submitted")
        code, body = rp.review_task(
            store, {"decision": ACCEPTED, "note": "skip work"}, {"id": task.id})
        self.assertEqual(code, 409)
        self.assertIn("submitted", body["error"])
        self.assertEqual(store.get_task(task.id).state, QUEUED)

    def test_claim_bundles_accepted_dependency_outputs(self):
        store = self.active_store()
        worker = store.register_worker("Bob", "cli").id
        first = store.add_task("Research", "find it")
        second = store.add_task("Build", "use it", depends_on=[first.id])
        store.claim_next(worker)
        store.submit(worker, first.id, "dependency result")
        store.review(first.id, ACCEPTED)

        claim = store.claim_next(worker)
        self.assertEqual(claim["task"]["id"], second.id)
        self.assertEqual(claim["dependency_outputs"], [{
            "task_id": first.id,
            "title": first.title,
            "output": "dependency result",
        }])

        class NoFetchClient:
            def tasks(self):
                raise AssertionError("bundled dependency data should avoid a second request")

        self.assertEqual(claim_dependency_outputs(NoFetchClient(), claim),
                         [(first.title, "dependency result")])

    def test_stale_worker_emits_refresh_events(self):
        events = []
        store = self.active_store(events)
        worker = store.register_worker("Carol", "cli").id
        task = store.add_task("In flight", "work")
        store.claim_next(worker)
        events.clear()
        store._workers[worker].last_seen = time.time() - WORKER_TIMEOUT - 1

        store.list_workers()
        names = [name for name, _ in events]
        self.assertIn("worker_offline", names)
        self.assertIn("task_created", names)
        self.assertEqual(store.get_task(task.id).state, QUEUED)


class LongPollHttpTests(unittest.TestCase):
    def setUp(self):
        self.original_store = app.STORE
        app.STORE = Store()
        app.STORE.create_project("long poll")
        app.STORE.set_state(ACTIVE)
        self.worker = app.STORE.register_worker("Dana", "cli").id
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        app.STORE = self.original_store

    def test_post_wait_query_holds_until_work_arrives(self):
        def add_work():
            time.sleep(0.12)
            app.STORE.add_task("Arrived", "after request")

        threading.Thread(target=add_work, daemon=True).start()
        url = (f"http://127.0.0.1:{self.httpd.server_address[1]}"
               f"/api/worker/{self.worker}/next?wait=1")
        request = urllib.request.Request(
            url, data=b"{}", method="POST", headers={"content-type": "application/json"})
        started = time.monotonic()
        with urllib.request.urlopen(request, timeout=2) as response:
            payload = json.loads(response.read())
        elapsed = time.monotonic() - started

        self.assertEqual(payload["task"]["title"], "Arrived")
        self.assertGreaterEqual(elapsed, 0.09)
        self.assertLess(elapsed, 0.8)

    def test_health_advertises_worker_lan_url(self):
        url = f"http://127.0.0.1:{self.httpd.server_address[1]}/api/health"
        with urllib.request.urlopen(url, timeout=2) as response:
            payload = json.loads(response.read())
        self.assertRegex(payload["lan_url"], r"^http://[^:]+:\d+$")


if __name__ == "__main__":
    unittest.main()
