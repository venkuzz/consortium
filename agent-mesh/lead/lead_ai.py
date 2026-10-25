"""
Uniform LLM wrapper over anthropic / openai / kimi / mock.

Same pattern as `agent-mesh/agents.py`: stdlib only (urllib), one entry point,
the rest of the system never knows which provider is behind it. Both the Lead AI
(`LeadAI`) and the worker side (`worker/worker_agent.py`) call `complete()`.

Env:
  LEAD_PROVIDER      anthropic|openai|kimi|mock  (default: first one with a key, else mock)
  LEAD_MODEL         model override for the lead
  ANTHROPIC_API_KEY / OPENAI_API_KEY / KIMI_API_KEY
  ANTHROPIC_MODEL / OPENAI_MODEL / KIMI_MODEL
  KIMI_BASE_URL      OpenAI-compatible base URL (default Moonshot)
  LLM_TIMEOUT        seconds per request (default 90)
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional

PROVIDERS = ("anthropic", "openai", "kimi", "mock")

DEFAULT_MODELS = {
    "anthropic": os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5"),
    "openai": os.getenv("OPENAI_MODEL", "gpt-4o"),
    "kimi": os.getenv("KIMI_MODEL", "moonshot-v1-8k"),
}
KEY_ENV = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY", "kimi": "KIMI_API_KEY"}
KIMI_BASE_URL = os.getenv("KIMI_BASE_URL", "https://api.moonshot.ai/v1")
TIMEOUT = float(os.getenv("LLM_TIMEOUT", "90"))


class LLMError(Exception):
    pass


def _post_json(url: str, headers: dict, payload: dict) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise LLMError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")
    except urllib.error.URLError as e:
        raise LLMError(f"Network error: {e.reason}")
    except (TimeoutError, OSError) as e:
        raise LLMError(f"Network error: {e}")


def _anthropic(system: str, user: str, key: str, model: str, max_tokens: int) -> str:
    data = _post_json(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": key, "anthropic-version": "2023-06-01",
         "content-type": "application/json"},
        {"model": model, "max_tokens": max_tokens, "system": system,
         "messages": [{"role": "user", "content": user}]},
    )
    return "".join(b.get("text", "") for b in data.get("content", [])
                   if b.get("type") == "text")


def _openai_compatible(base_url: str, system: str, user: str, key: str,
                       model: str, max_tokens: int) -> str:
    data = _post_json(
        f"{base_url.rstrip('/')}/chat/completions",
        {"Authorization": f"Bearer {key}", "content-type": "application/json"},
        {"model": model, "temperature": 0.3, "max_tokens": max_tokens,
         "messages": [{"role": "system", "content": system},
                      {"role": "user", "content": user}]},
    )
    try:
        return data["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError):
        raise LLMError(f"Unexpected response shape: {str(data)[:200]}")


def resolve_key(provider: str, api_key: Optional[str] = None) -> Optional[str]:
    return api_key or (os.getenv(KEY_ENV[provider]) if provider in KEY_ENV else None)


def complete(provider: str, system: str, user: str, api_key: Optional[str] = None,
             model: Optional[str] = None, max_tokens: int = 4096) -> str:
    """One LLM call. Raises LLMError on any failure (callers decide how to degrade).

    `mock` is not handled here — callers produce their own canned output."""
    if provider not in KEY_ENV:
        raise LLMError(f"Provider {provider!r} has no LLM backend")
    key = resolve_key(provider, api_key)
    if not key:
        raise LLMError(f"No API key for {provider} (set {KEY_ENV[provider]})")
    model = model or DEFAULT_MODELS[provider]
    if provider == "anthropic":
        text = _anthropic(system, user, key, model, max_tokens)
    elif provider == "openai":
        text = _openai_compatible("https://api.openai.com/v1", system, user, key, model, max_tokens)
    else:
        text = _openai_compatible(KIMI_BASE_URL, system, user, key, model, max_tokens)
    text = text.strip()
    if not text:
        raise LLMError(f"{provider} returned an empty response")
    return text


def _auto_provider() -> str:
    for p in ("anthropic", "openai", "kimi"):
        if os.getenv(KEY_ENV[p]):
            return p
    return "mock"


class LeadAI:
    """The leader's attached AI. `mock` (or no key) means: orchestrator uses templates."""

    def __init__(self, provider: Optional[str] = None, api_key: Optional[str] = None,
                 model: Optional[str] = None):
        provider = provider or os.getenv("LEAD_PROVIDER") or _auto_provider()
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}; expected one of {PROVIDERS}")
        self.provider = provider
        self.api_key = api_key
        self.model = model or os.getenv("LEAD_MODEL") or None

    @property
    def available(self) -> bool:
        return self.provider != "mock" and bool(resolve_key(self.provider, self.api_key))

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model or DEFAULT_MODELS.get(self.provider, 'canned')}"

    def complete(self, system: str, user: str, max_tokens: int = 4096) -> str:
        if self.provider == "mock":
            raise LLMError("mock lead has no LLM")
        return complete(self.provider, system, user, self.api_key, self.model, max_tokens)


_lead: Optional[LeadAI] = None


def get_lead() -> LeadAI:
    global _lead
    if _lead is None:
        _lead = LeadAI()
    return _lead


def configure(provider: Optional[str] = None, api_key: Optional[str] = None,
              model: Optional[str] = None) -> LeadAI:
    """Let the server swap the Lead AI at runtime (e.g. leader picks it in the UI)."""
    global _lead
    _lead = LeadAI(provider, api_key, model)
    return _lead
