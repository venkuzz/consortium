"""Agent B tests: planning JSON parsing + fallback, review paths, worker wrapper, client loop.

Run:  python tests/test_orchestrator.py   (from consortium-leader-worker/)
No keys or network needed — the Lead AI is stubbed and the server is faked in-process.
"""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["MOCK_DELAY"] = "0"

from lead import lead_ai, orchestrator  # noqa: E402
from worker import agent_client, worker_agent  # noqa: E402

worker_agent.MOCK_DELAY = 0.0


class FakeLead:
    """Stands in for LeadAI: returns queued replies, or raises if a reply is an Exception."""
    available = True
    label = "fake:test"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def complete(self, system, user, max_tokens=4096):
        self.calls.append((system, user))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def use_lead(lead):
    lead_ai._lead = lead


class PlanTests(unittest.TestCase):
    def tearDown(self):
        lead_ai._lead = None

    def test_no_lead_uses_template(self):
        use_lead(lead_ai.LeadAI("mock"))
        out = orchestrator.plan("landing page")
        self.assertFalse(out["used_llm"])
        self.assertGreaterEqual(len(out["tasks"]), 3)
        last = out["tasks"][-1]
        self.assertEqual(last["depends_on"], list(range(len(out["tasks"]) - 1)))
        for t in out["tasks"]:
            self.assertEqual(set(t), {"title", "instruction", "depends_on"})
            self.assertIn("landing page", t["instruction"])

    def test_llm_plan_with_fences_and_prose(self):
        reply = ("Sure! Here's the plan:\n```json\n" + json.dumps({"tasks": [
            {"title": "A", "instruction": "do a", "depends_on": []},
            {"title": "B", "instruction": "do b", "depends_on": ["0"]},
        ]}) + "\n```\nGood luck!")
        use_lead(FakeLead(reply))
        out = orchestrator.plan("goal")
        self.assertTrue(out["used_llm"])
        self.assertEqual([t["title"] for t in out["tasks"]], ["A", "B"])
        self.assertEqual(out["tasks"][1]["depends_on"], [0])

    def test_llm_plan_drops_bad_tasks_and_forward_deps(self):
        reply = json.dumps({"tasks": [
            {"title": "A", "instruction": "do a", "depends_on": [1, 5, "x"]},  # forward/bogus
            {"title": "empty", "instruction": ""},                             # dropped
            "not a dict",                                                      # dropped
            {"title": "C", "instruction": "do c", "depends_on": [0, 0, 1]},    # 1 was dropped
        ]})
        use_lead(FakeLead(reply))
        tasks = orchestrator.plan("g")["tasks"]
        self.assertEqual([t["title"] for t in tasks], ["A", "C"])
        self.assertEqual(tasks[0]["depends_on"], [])
        self.assertEqual(tasks[1]["depends_on"], [0])

    def test_llm_junk_falls_back(self):
        for bad in ["no json here", json.dumps({"tasks": []}), json.dumps({"x": 1}),
                    lead_ai.LLMError("HTTP 500")]:
            use_lead(FakeLead(bad))
            out = orchestrator.plan("g")
            self.assertFalse(out["used_llm"], bad)
            self.assertIn("template", out["note"])

    def test_task_cap(self):
        many = [{"title": f"t{i}", "instruction": "x"} for i in range(20)]
        use_lead(FakeLead(json.dumps({"tasks": many})))
        self.assertEqual(len(orchestrator.plan("g")["tasks"]), orchestrator.MAX_TASKS)


class ReviewTests(unittest.TestCase):
    task = {"title": "Hero", "instruction": "Build #hero"}

    def tearDown(self):
        lead_ai._lead = None

    def test_empty_result_rejected_without_llm_call(self):
        lead = FakeLead()
        use_lead(lead)
        out = orchestrator.review(self.task, "   ")
        self.assertEqual(out["decision"], "rejected")
        self.assertIn("Build #hero", out["followup_instruction"])
        self.assertEqual(lead.calls, [])

    def test_no_lead_accepts(self):
        use_lead(lead_ai.LeadAI("mock"))
        out = orchestrator.review(self.task, "<section id='hero'>")
        self.assertEqual(out, {"decision": "accepted", "note": out["note"],
                               "followup_instruction": None})

    def test_llm_accept(self):
        use_lead(FakeLead('{"decision": "Accepted", "note": "ok", "followup_instruction": null}'))
        out = orchestrator.review(self.task, "x")
        self.assertEqual((out["decision"], out["note"], out["followup_instruction"]),
                         ("accepted", "ok", None))

    def test_llm_reject_with_and_without_followup(self):
        use_lead(FakeLead('{"decision":"rejected","note":"no ids","followup_instruction":"redo"}'))
        self.assertEqual(orchestrator.review(self.task, "x")["followup_instruction"], "redo")
        use_lead(FakeLead('{"decision":"rejected","note":"no ids"}'))
        f = orchestrator.review(self.task, "x")["followup_instruction"]
        self.assertIn("Build #hero", f)
        self.assertIn("no ids", f)

    def test_llm_failure_or_bad_decision_accepts(self):
        for bad in [lead_ai.LLMError("timeout"), '{"decision":"maybe"}', "garbage"]:
            use_lead(FakeLead(bad))
            self.assertEqual(orchestrator.review(self.task, "x")["decision"], "accepted")


class SummaryTests(unittest.TestCase):
    def tearDown(self):
        lead_ai._lead = None

    def test_counts(self):
        use_lead(lead_ai.LeadAI("mock"))
        tasks = [{"seq": 1, "state": "accepted", "title": "A"},
                 {"seq": 2, "state": "assigned", "title": "B"},
                 {"seq": 3, "state": "queued", "title": "C"}]
        s = orchestrator.summarize({"goal": "g"}, tasks)
        self.assertIn("1/3 tasks accepted", s)
        self.assertIn("1 assigned", s)
        self.assertIn("Latest done: A", s)
        self.assertEqual(orchestrator.summarize({}, []), "No tasks yet.")

    def test_llm_summary_falls_back(self):
        use_lead(FakeLead(lead_ai.LLMError("down")))
        s = orchestrator.summarize({"goal": "g"}, [{"seq": 1, "state": "queued", "title": "A"}],
                                   use_llm=True)
        self.assertIn("0/1", s)


class WorkerAgentTests(unittest.TestCase):
    def test_mock_routes_by_title(self):
        tasks = orchestrator._template_plan("g")
        outs = [worker_agent.run_worker("mock", t)["output"] for t in tasks]
        self.assertIn("<section", outs[0])
        self.assertIn("<style>", outs[1])
        self.assertIn("<script>", outs[3])
        self.assertIn("<!DOCTYPE html>", outs[4])

    def test_failures_never_raise(self):
        r = worker_agent.run_worker("nope", {"title": "x"})
        self.assertFalse(r["ok"])
        saved = os.environ.pop("KIMI_API_KEY", None)
        try:
            r = worker_agent.run_worker("kimi", {"title": "x", "instruction": "y"})
            self.assertFalse(r["ok"])
            self.assertIn("No API key", r["error"])
        finally:
            if saved:
                os.environ["KIMI_API_KEY"] = saved


# ---------- fake §4 server for the client loop ----------

class FakeServer:
    def __init__(self, specs):
        self.lock = threading.Lock()
        self.state = "active"
        self.workers = {}
        self.tasks = [{"id": f"t_{i + 1}", "seq": i + 1, "title": s["title"],
                       "instruction": s["instruction"],
                       "depends_on": [f"t_{d + 1}" for d in s["depends_on"]],
                       "state": "queued", "assigned_to": None, "result": ""}
                      for i, s in enumerate(specs)]
        self.submissions = []
        fake = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                data = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/tasks":
                    with fake.lock:
                        return self._send(200, {"tasks": fake.tasks})
                self._send(404, {"error": "not found"})

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(n) or b"{}")
                parts = self.path.split("?")[0].strip("/").split("/")
                if parts == ["api", "worker", "register"]:
                    wid = f"{body['owner'].lower()}-{len(fake.workers)}"
                    fake.workers[wid] = body
                    return self._send(200, {"worker": {"id": wid, "display": body["owner"]},
                                            "project_state": fake.state})
                if len(parts) != 4 or parts[3] not in ("heartbeat", "next", "submit", "unregister"):
                    return self._send(404, {"error": "not found"})
                wid, action = parts[2], parts[3]
                if wid not in fake.workers:
                    return self._send(404, {"error": "unknown worker"})
                with fake.lock:
                    if action == "heartbeat":
                        return self._send(200, {"ok": True, "project_state": fake.state})
                    if action == "next":
                        if fake.state != "active":
                            return self._send(200, {"task": None, "stop": True})
                        done = {t["id"] for t in fake.tasks if t["state"] == "accepted"}
                        for t in fake.tasks:
                            if t["state"] == "queued" and set(t["depends_on"]) <= done:
                                t["state"], t["assigned_to"] = "assigned", wid
                                return self._send(200, {"task": t, "project": {
                                    "id": "p", "goal": "test goal", "summary": ""}})
                        return self._send(200, {"task": None, "wait": True})
                    if action == "submit":
                        t = next(t for t in fake.tasks if t["id"] == body["task_id"])
                        fake.submissions.append((wid, body))
                        # auto-accept, like the server would via orchestrator.review
                        t["state"], t["result"] = "accepted", body["output"]
                        if all(x["state"] == "accepted" for x in fake.tasks):
                            fake.state = "done"
                        return self._send(200, {"ok": True, "task_state": "submitted"})
                    fake.workers.pop(wid)
                    return self._send(200, {"ok": True})

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


class ClientLoopTests(unittest.TestCase):
    def test_two_workers_finish_all_tasks_then_stop(self):
        fake = FakeServer(orchestrator._template_plan("g"))
        try:
            counts = {}

            def go(name):
                c = agent_client.ConsortiumClient(fake.url, timeout=5)
                counts[name] = agent_client.run_loop(c, name, "mock", poll=0.05,
                                                     log=lambda m: None)

            threads = [threading.Thread(target=go, args=(n,)) for n in ("Alice", "Bob")]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=20)
            self.assertFalse(any(t.is_alive() for t in threads), "client loops did not stop")
            self.assertEqual(fake.state, "done")
            self.assertEqual(sum(counts.values()), len(fake.tasks))
            self.assertTrue(all(b["ok"] for _, b in fake.submissions))
            self.assertEqual({k for k in ("task_id", "output", "ok", "error", "elapsed")},
                             set(fake.submissions[0][1]))
        finally:
            fake.close()

    def test_dependency_outputs_are_fetched(self):
        fake = FakeServer(orchestrator._template_plan("g"))
        try:
            c = agent_client.ConsortiumClient(fake.url, timeout=5)
            for t in fake.tasks[:4]:
                t["state"], t["result"] = "accepted", f"out-{t['id']}"
            deps = agent_client.dependency_outputs(c, fake.tasks[4])
            self.assertEqual([o for _, o in deps], ["out-t_1", "out-t_2", "out-t_3", "out-t_4"])
        finally:
            fake.close()

    def test_api_key_is_not_sent_to_server(self):
        fake = FakeServer([{"title": "only", "instruction": "x", "depends_on": []}])
        try:
            c = agent_client.ConsortiumClient(fake.url, timeout=5)
            agent_client.run_loop(c, "Eve", "mock", api_key="sk-secret", poll=0.05,
                                  log=lambda m: None)
            self.assertNotIn("sk-secret", json.dumps(list(fake.workers.values())))
            self.assertNotIn("sk-secret", json.dumps(fake.submissions))
        finally:
            fake.close()


if __name__ == "__main__":
    unittest.main()
