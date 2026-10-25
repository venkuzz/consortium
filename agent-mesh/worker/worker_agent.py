"""
Uniform Worker AI wrapper (same pattern as `agent-mesh/agents.py`).

`run_worker()` never raises: failures become ok=False so the client can still
submit and the lead can reject/requeue. Providers: anthropic | openai | kimi | mock.
"""
from __future__ import annotations

import os
import re
import sys
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lead import prompts  # noqa: E402
from lead.lead_ai import PROVIDERS, complete  # noqa: E402

MOCK_DELAY = float(os.getenv("MOCK_DELAY", "1.0"))
_FENCE = re.compile(r"^```[\w+-]*[ \t]*\n(.*?)\n?```$", re.DOTALL)


def strip_fence(text: str) -> str:
    """LLMs often wrap the whole deliverable in ```html ... ```; unwrap one outer fence."""
    m = _FENCE.match(text.strip())
    return m.group(1).strip() if m else text


def _mock_output(title: str, instruction: str) -> str:
    """Canned output keyed off the task wording so mock demos still look plausible."""
    time.sleep(MOCK_DELAY)
    text = (title or instruction[:80]).lower()  # titles are less ambiguous than instructions
    if "integrat" in text or "assemble" in text:
        return ("<!DOCTYPE html>\n<html><head><meta charset='utf-8'><title>Mock build</title></head>\n"
                "<body><section id='hero'><h1>Built by the team</h1></section></body></html>")
    if "css" in text or "styl" in text:
        return ("<style>\n  body{font-family:system-ui,sans-serif;margin:0;background:#0f172a;color:#e2e8f0}\n"
                "  #hero{padding:56px 24px;text-align:center;background:linear-gradient(135deg,#6366f1,#8b5cf6)}\n"
                "  .card{background:#1e293b;border-radius:12px;padding:20px;margin:12px}\n"
                "  .btn{background:#6366f1;color:#fff;border:0;padding:12px 20px;border-radius:8px}\n</style>")
    if "js" in text or "script" in text or "interactiv" in text:
        return ("<script>\ndocument.getElementById('signup-form')?.addEventListener('submit', e => {\n"
                "  e.preventDefault();\n  const v = document.getElementById('email').value;\n"
                "  e.target.insertAdjacentHTML('afterend', `<p>Thanks, ${v || 'friend'}!</p>`);\n});\n</script>")
    if "html" in text or "markup" in text or "structure" in text:
        return ("<section id='hero'><h1>Headline</h1><button class='btn'>Get started</button></section>\n"
                "<section id='features'><div class='card'>Feature</div></section>\n"
                "<section id='signup'><form id='signup-form'><input id='email'>"
                "<button id='join' class='btn'>Join</button></form></section>\n<footer id='footer'></footer>")
    return f"[mock result] Completed: {title or instruction[:60]}"


def run_worker(provider: str, task: dict, project: Optional[dict] = None,
               dependency_outputs: Optional[list[tuple[str, str]]] = None,
               api_key: Optional[str] = None, model: Optional[str] = None) -> dict:
    """Execute one task with the worker's AI.

    -> {'ok': bool, 'output': str, 'error': str, 'elapsed': float}
    (matches the body of POST /api/worker/{id}/submit minus task_id)."""
    start = time.time()
    title = task.get("title", "")
    instruction = task.get("instruction", "")
    try:
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}")
        if provider == "mock":
            out = _mock_output(title, instruction)
        else:
            project = project or {}
            user = prompts.worker_user(project.get("goal", ""), project.get("summary", ""),
                                       title, instruction, dependency_outputs or [])
            out = strip_fence(complete(provider, prompts.WORKER_SYSTEM, user, api_key, model,
                                       max_tokens=8000))
        return {"ok": True, "output": out, "error": "", "elapsed": round(time.time() - start, 2)}
    except Exception as e:  # graceful per-worker failure
        return {"ok": False, "output": "", "error": str(e)[:500],
                "elapsed": round(time.time() - start, 2)}
