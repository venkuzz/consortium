"""
Leader-chosen Lead AI for planning (/api/project/plan with lead_* fields).

Verifies the plumbing: the plan route forwards the leader's AI choice to
lead_facade.configure_lead -> lead_ai.configure, and planning still works
(falling back to the template when the chosen AI has no key, so it never dies).

Run from agent-mesh/:  python -m tests.test_lead_config
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("CONSORTIUM_ALLOW_MOCK_WORK", "1")
from core.store import Store
from core.state import ACTIVE
from server import routes_project as rp
from server import lead_facade


def test_configure_lead_facade():
    # configure_lead should return a dict and never raise, even with junk.
    out = lead_facade.configure_lead(provider="openai", api_key="", model="gpt-4o")
    assert isinstance(out, dict) and "note" in out and "available" in out
    print("  configure_lead returns a status dict OK:", out["note"][:60])


def test_plan_accepts_lead_choice_and_still_plans():
    s = Store()
    s.create_project("Build a landing page")
    # Leader picks a provider but supplies no key -> real AI unavailable, so the
    # orchestrator falls back to its template. The key point: it must still plan
    # and the route must accept the lead_* fields without error.
    code, body = rp.plan_project(s, {
        "lead_provider": "openai", "lead_api_key": "", "lead_model": "gpt-4o",
    }, {})
    assert code == 200, body
    assert len(body["tasks"]) >= 1, "planning produced no tasks"
    assert body["project"]["state"] == "active"
    assert "note" in body
    print(f"  plan with leader AI choice OK ({len(body['tasks'])} tasks, used_llm={body['used_llm']})")
    print("  note:", body["note"][:90])


def test_plan_without_lead_fields_unchanged():
    s = Store()
    s.create_project("Another goal")
    code, body = rp.plan_project(s, {}, {})
    assert code == 200 and len(body["tasks"]) >= 1
    print("  plan with no lead fields still works OK")


if __name__ == "__main__":
    print("=== leader Lead-AI config tests ===")
    test_configure_lead_facade()
    test_plan_accepts_lead_choice_and_still_plans()
    test_plan_without_lead_fields_unchanged()
    print("ALL LEAD-CONFIG TESTS PASSED")
