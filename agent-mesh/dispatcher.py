"""
The dispatcher: an LLM reads the one team prompt and decides what each
agent should build, filling FIXED slots. Structure is deterministic
(always 2 slots -> clean merge); the per-slot instructions are LLM-generated.

If the LLM dispatch call fails or returns junk, we fall back to a plain
fixed split so the demo always runs. The LLM is an enhancement, not a dependency.
"""
from __future__ import annotations

import os
import json
import re
from typing import Dict, List

from agents import Agent, OPENAI_MODEL, _post_json, ProviderError

# Fixed slots. The merge logic depends on exactly these two.
SLOT_A = "structure"   # HTML structure + JS behavior
SLOT_B = "style"       # CSS styling + text content

SLOT_DESCRIPTIONS = {
    SLOT_A: "the HTML document structure and any JavaScript interactivity",
    SLOT_B: "the CSS styling (in a <style> block) and the user-facing text content",
}

DISPATCH_SYSTEM = """You are the orchestrator for a team of AI coding agents at a hackathon.
The team gave you ONE build request. You must split it into exactly two sub-tasks
that fill fixed slots, then return STRICT JSON only.

Slots:
- "structure": instructions for the agent building the HTML structure + JavaScript behavior.
- "style": instructions for the agent building the CSS styling + the visible text content.

Rules:
- Each sub-task must be self-contained and reference the SAME shared element ids/classes
  so the two outputs merge into one coherent page.
- Return ONLY JSON of the form:
  {"structure": "<instructions>", "style": "<instructions>"}
- No prose, no markdown fences.
"""


def _fixed_split(team_prompt: str) -> Dict[str, str]:
    """Deterministic fallback used when the LLM dispatcher is unavailable."""
    return {
        SLOT_A: (
            f"Build ONLY the HTML structure and any JavaScript for this request:\n"
            f"'{team_prompt}'\n"
            f"Use clear, conventional element ids and classes. Do not include CSS. "
            f"Return a complete HTML snippet (body content + optional <script>)."
        ),
        SLOT_B: (
            f"Build ONLY the CSS (inside a <style> block) and the visible text content "
            f"for this request:\n'{team_prompt}'\n"
            f"Assume conventional ids/classes (header, main, .card, .btn, etc.). "
            f"Make it look polished. Return a <style> block plus any content notes."
        ),
    }


def _extract_json(text: str) -> Dict[str, str]:
    """Pull the first JSON object out of a model response, tolerate fences."""
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ValueError("no JSON object found in dispatcher output")
    obj = json.loads(match.group(0))
    if SLOT_A not in obj or SLOT_B not in obj:
        raise ValueError("dispatcher JSON missing required slots")
    return {SLOT_A: str(obj[SLOT_A]), SLOT_B: str(obj[SLOT_B])}


def dispatch(team_prompt: str) -> Dict:
    """Return {'slots': {...}, 'used_llm': bool, 'note': str}."""
    key = os.getenv("OPENAI_API_KEY")
    if not key:
        return {"slots": _fixed_split(team_prompt), "used_llm": False,
                "note": "No OPENAI_API_KEY; used deterministic fixed split."}
    try:
        data = _post_json(
            "https://api.openai.com/v1/chat/completions",
            {"Authorization": f"Bearer {key}", "content-type": "application/json"},
            {"model": OPENAI_MODEL, "temperature": 0.2,
             "messages": [
                 {"role": "system", "content": DISPATCH_SYSTEM},
                 {"role": "user", "content": f"Team build request: {team_prompt}"},
             ]},
        )
        content = data["choices"][0]["message"]["content"]
        slots = _extract_json(content)
        return {"slots": slots, "used_llm": True,
                "note": "LLM dispatcher assigned the sub-tasks."}
    except Exception as e:
        return {"slots": _fixed_split(team_prompt), "used_llm": False,
                "note": f"Dispatcher LLM failed ({e}); fell back to fixed split."}


def assign_agents_to_slots(agents: List[Agent]) -> Dict[str, Agent]:
    """Map the two fixed slots onto the available agents.

    With 2 agents: agent[0] -> structure, agent[1] -> style.
    With 1 agent: it does both slots sequentially (handled by caller).
    With >2 agents: extras double up on slots round-robin (still merges).
    """
    mapping: Dict[str, Agent] = {}
    if not agents:
        return mapping
    slots = [SLOT_A, SLOT_B]
    for i, slot in enumerate(slots):
        mapping[slot] = agents[i % len(agents)]
    return mapping


def merge(structure_html: str, style_block: str) -> str:
    """Combine the two agents' pieces into one standalone HTML document."""
    # If the style agent returned a full <style>...</style>, keep it; else wrap it.
    style = style_block.strip()
    if "<style" not in style.lower():
        style = f"<style>\n{style}\n</style>"
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Agent Mesh Output</title>
{style}
</head>
<body>
{structure_html}
</body>
</html>"""
