"""
Uniform Worker AI wrapper (same pattern as `agent-mesh/agents.py`).

`run_worker()` never raises: failures become ok=False so the client can still
submit and the lead can reject/requeue.
Providers: anthropic | openai | kimi | custom | cli | mock.

The `cli` provider drives the teammate's OWN command-line agent (Claude Code,
Codex CLI, aider, or any script) instead of calling a model API: the lead's
instruction is piped to the command on stdin and its stdout becomes the result.
No model API key is involved — the CLI tool uses its own auth. This is the
"most CLI" path: a teammate's existing terminal agent becomes a mesh worker.
"""
from __future__ import annotations

import os
import re
import sys
import time
import shlex
import subprocess
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from lead import prompts  # noqa: E402
from lead.lead_ai import PROVIDERS, complete  # noqa: E402

MOCK_DELAY = float(os.getenv("MOCK_DELAY", "1.0"))
# Default command for the `cli` provider; override via --cli-cmd / CONSORTIUM_CLI_CMD.
# "{prompt}" (if present) is substituted with the instruction; otherwise the
# prompt is piped to the command's stdin.
CLI_CMD = os.getenv("CONSORTIUM_CLI_CMD", "")
CLI_TIMEOUT = float(os.getenv("CONSORTIUM_CLI_TIMEOUT", "300"))
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


def _build_cli_prompt(project: dict, title: str, instruction: str,
                      dependency_outputs: list[tuple[str, str]]) -> str:
    """A self-contained prompt for a CLI agent (no system/user split)."""
    parts = [
        "You are a worker on a collaborative build team. Do exactly the task below.",
        "You are running inside the worker's current directory. If the task concerns",
        "repository files, inspect and edit those files directly and run relevant checks.",
        "Do not push or merge unless the task explicitly requests it. End with a concise",
        "summary of the completed work and checks; that final response is sent to the lead.",
        "",
        f"Project goal: {project.get('goal', '')}",
    ]
    if project.get("summary"):
        parts.append(f"Progress so far: {project['summary']}")
    parts += ["", f"Task: {title}", f"Instruction:\n{instruction}"]
    for dep_title, dep_out in dependency_outputs or []:
        parts.append(f"\n--- output of dependency '{dep_title}' ---\n{dep_out}")
    return "\n".join(parts)


def _run_cli(cmd: str, prompt: str) -> str:
    """Drive a teammate's own CLI agent. The prompt is substituted into {prompt}
    if the command contains that token, otherwise piped to stdin. stdout is the
    result. The CLI tool handles its own auth — no model API key here.

    Tokenized with POSIX rules on all platforms so wrapping quotes around an
    argument (e.g. `-c "..."`) are stripped correctly. On Windows this means
    backslash path separators in the command must be quoted or written with
    forward slashes; real CLI agents (claude/aider/codex) are bare PATH names,
    so this is rarely an issue."""
    if not cmd:
        raise ValueError("cli provider needs a command (set --cli-cmd / CONSORTIUM_CLI_CMD), "
                         "e.g. 'claude -p' or 'aider --message-file -'")
    if "{prompt}" in cmd:
        argv = shlex.split(cmd.replace("{prompt}", _q(prompt)), posix=True)
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


def _q(s: str) -> str:
    """Quote a prompt for {prompt}-substitution so it survives POSIX shlex.split."""
    return "'" + s.replace("'", "'\\''") + "'"


def run_worker(provider: str, task: dict, project: Optional[dict] = None,
               dependency_outputs: Optional[list[tuple[str, str]]] = None,
               api_key: Optional[str] = None, model: Optional[str] = None,
               base_url: Optional[str] = None, cli_cmd: Optional[str] = None) -> dict:
    """Execute one task with the worker's AI.

    -> {'ok': bool, 'output': str, 'error': str, 'elapsed': float}
    (matches the body of POST /api/worker/{id}/submit minus task_id).
    `base_url` targets any OpenAI-compatible API for provider='custom'.
    `cli_cmd` is the command to run for provider='cli' (teammate's own agent)."""
    start = time.time()
    title = task.get("title", "")
    instruction = task.get("instruction", "")
    try:
        if provider not in PROVIDERS:
            raise ValueError(f"unknown provider {provider!r}")
        project = project or {}
        if provider == "mock":
            out = _mock_output(title, instruction)
        elif provider == "cli":
            prompt = _build_cli_prompt(project, title, instruction, dependency_outputs or [])
            out = strip_fence(_run_cli(cli_cmd or CLI_CMD, prompt))
        else:
            user = prompts.worker_user(project.get("goal", ""), project.get("summary", ""),
                                       title, instruction, dependency_outputs or [])
            out = strip_fence(complete(provider, prompts.WORKER_SYSTEM, user, api_key, model,
                                       max_tokens=8000, base_url=base_url))
        return {"ok": True, "output": out, "error": "", "elapsed": round(time.time() - start, 2)}
    except Exception as e:  # graceful per-worker failure
        return {"ok": False, "output": "", "error": str(e)[:500],
                "elapsed": round(time.time() - start, 2)}
