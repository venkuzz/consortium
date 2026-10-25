"""
Agent layer: a uniform wrapper over heterogeneous AI providers.

Zero third-party dependencies — stdlib only (urllib) so it runs on any
machine with Python, no pip install. The rest of the system only ever calls
`run_agent(agent, prompt)` and never knows which provider is behind it.
This is what lets a stranger's AI slot in with a one-line change.
"""
from __future__ import annotations

import os
import time
import json
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import Optional

ANTHROPIC_MODEL = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o")
REQUEST_TIMEOUT = float(os.getenv("AGENT_TIMEOUT", "90"))


@dataclass
class Agent:
    """An agent brought by a team member."""
    id: str                        # stable id, e.g. "alice-claude-1a2b"
    owner: str                     # human name, e.g. "Alice"
    provider: str                  # "anthropic" | "openai" | "mock"
    display: str                   # label shown in UI, e.g. "Alice's Claude"
    api_key: Optional[str] = None  # per-agent key, falls back to env
    model: Optional[str] = None


@dataclass
class AgentResult:
    agent_id: str
    display: str
    ok: bool
    output: str = ""
    error: str = ""
    elapsed: float = 0.0


class ProviderError(Exception):
    pass


def _post_json(url: str, headers: dict, payload: dict) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:300]
        raise ProviderError(f"HTTP {e.code}: {body}")
    except urllib.error.URLError as e:
        raise ProviderError(f"Network error: {e.reason}")


def _call_anthropic(prompt: str, key: str, model: str) -> str:
    data = _post_json(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": key, "anthropic-version": "2023-06-01",
         "content-type": "application/json"},
        {"model": model, "max_tokens": 4096,
         "messages": [{"role": "user", "content": prompt}]},
    )
    parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
    text = "".join(parts).strip()
    if not text:
        raise ProviderError("Anthropic returned empty response")
    return text


def _call_openai(prompt: str, key: str, model: str) -> str:
    data = _post_json(
        "https://api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {key}", "content-type": "application/json"},
        {"model": model, "temperature": 0.4,
         "messages": [{"role": "user", "content": prompt}]},
    )
    text = (data["choices"][0]["message"]["content"] or "").strip()
    if not text:
        raise ProviderError("OpenAI returned empty response")
    return text


def _call_mock(prompt: str, agent: Agent, slot: Optional[str] = None) -> str:
    """Canned provider so the demo runs even without a key for an agent.

    Routing is driven by the explicit `slot` the pipeline assigned, not by
    guessing from prompt keywords (which are ambiguous)."""
    time.sleep(1.0)  # pretend we did work; keeps UI timing realistic
    is_style = (slot == "style")
    if is_style:
        return (
            "<style>\n"
            "  body{font-family:system-ui,sans-serif;margin:0;background:#0f172a;color:#e2e8f0}\n"
            "  header{padding:48px 24px;text-align:center;background:linear-gradient(135deg,#6366f1,#8b5cf6)}\n"
            "  header h1{margin:0;font-size:2.2rem}\n"
            "  main{max-width:720px;margin:0 auto;padding:32px 24px}\n"
            "  .card{background:#1e293b;border:1px solid #334155;border-radius:12px;padding:20px;margin:12px 0}\n"
            "  .btn{display:inline-block;background:#6366f1;color:#fff;border:none;padding:12px 20px;border-radius:8px;cursor:pointer}\n"
            "  input{padding:10px;border-radius:8px;border:1px solid #334155;background:#0f172a;color:#e2e8f0;width:100%;box-sizing:border-box;margin:6px 0}\n"
            "</style>\n"
            f"<!-- mock styling from {agent.display} -->"
        )
    return (
        f"<!-- mock structure from {agent.display} -->\n"
        "<header><h1>Demo Page</h1><p>Built by the agent mesh</p></header>\n"
        "<main>\n"
        "  <div class='card'><h2>Welcome</h2><p>This page was assembled from "
        "independent pieces produced by different teammates' AIs in parallel.</p></div>\n"
        "  <div class='card'><h2>Sign up</h2>\n"
        "    <input id='email' placeholder='you@example.com' />\n"
        "    <button class='btn' onclick=\"document.getElementById('msg').textContent='Thanks, '+ (document.getElementById('email').value||'friend') + '!'\">Join</button>\n"
        "    <p id='msg'></p>\n"
        "  </div>\n"
        "</main>"
    )


def _resolve_key(agent: Agent) -> Optional[str]:
    if agent.api_key:
        return agent.api_key
    if agent.provider == "anthropic":
        return os.getenv("ANTHROPIC_API_KEY")
    if agent.provider == "openai":
        return os.getenv("OPENAI_API_KEY")
    return None


def run_agent(agent: Agent, prompt: str, slot: Optional[str] = None) -> AgentResult:
    """Single uniform entry point. Never raises: failures become ok=False.

    `slot` is an optional hint (the fixed slot this agent was assigned);
    real providers ignore it, the mock uses it for deterministic output."""
    start = time.time()
    try:
        if agent.provider == "mock":
            out = _call_mock(prompt, agent, slot)
        else:
            key = _resolve_key(agent)
            if not key:
                raise ProviderError(f"No API key available for {agent.display}")
            if agent.provider == "anthropic":
                out = _call_anthropic(prompt, key, agent.model or ANTHROPIC_MODEL)
            elif agent.provider == "openai":
                out = _call_openai(prompt, key, agent.model or OPENAI_MODEL)
            else:
                raise ProviderError(f"Unknown provider: {agent.provider}")
        return AgentResult(agent.id, agent.display, ok=True, output=out,
                           elapsed=round(time.time() - start, 2))
    except Exception as e:  # graceful per-agent failure — the mesh survives
        return AgentResult(agent.id, agent.display, ok=False, error=str(e),
                           elapsed=round(time.time() - start, 2))
