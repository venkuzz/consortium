"""
Tests for the `cli` worker provider (WORK_SPLIT.md — "most CLI" path).

The cli provider drives a teammate's OWN command-line agent: the lead's
instruction is piped to a command on stdin (or substituted for {prompt}) and
stdout becomes the result. No model API key — the CLI tool uses its own auth.

We simulate a CLI agent with small Python one-liners (same contract as
`claude -p`, `aider --message-file -`, etc.: read prompt, print deliverable).

Run from agent-mesh/:  python -m tests.test_cli_provider
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from worker.worker_agent import run_worker
from lead.lead_ai import PROVIDERS

# Use forward slashes so the path survives POSIX shlex tokenization on Windows
# (real CLI agents like `claude`/`aider` are bare PATH names, so this only
# matters for the test fixture that invokes the Python interpreter by full path).
PY = sys.executable.replace("\\", "/")


def test_cli_stdin():
    """Prompt piped to the command's stdin; stdout captured as the result."""
    agent = f'{PY} -c "import sys;print(\'<cli-out>\'+sys.stdin.read().strip().splitlines()[-1]+\'</cli-out>\')"'
    task = {"title": "Hero", "instruction": "Build the hero markup with id #hero"}
    r = run_worker("cli", task, {"goal": "Landing page"}, cli_cmd=agent)
    assert r["ok"], r["error"]
    assert "<cli-out>" in r["output"]
    assert "#hero" in r["output"]  # last line of the prompt is the instruction
    print("  cli via stdin OK:", r["output"][:60])


def test_cli_prompt_substitution():
    """{prompt} token in the command is replaced with the instruction text."""
    agent = f'{PY} -c "import sys;print(\'got:\'+sys.argv[1][:20])" {{prompt}}'
    task = {"title": "X", "instruction": "make a footer"}
    r = run_worker("cli", task, {"goal": "g"}, cli_cmd=agent)
    assert r["ok"], r["error"]
    assert r["output"].startswith("got:")
    print("  cli via {prompt} substitution OK:", r["output"][:40])


def test_cli_missing_command():
    """No command configured -> graceful ok=False, not a crash."""
    r = run_worker("cli", {"title": "t", "instruction": "i"}, {"goal": "g"})
    assert r["ok"] is False
    assert "cli provider needs a command" in r["error"]
    print("  cli missing-command degrades gracefully OK")


def test_cli_command_not_found():
    """A bogus command -> graceful ok=False with a clear error."""
    r = run_worker("cli", {"title": "t", "instruction": "i"}, {"goal": "g"},
                   cli_cmd="definitely-not-a-real-binary-xyz --flag")
    assert r["ok"] is False
    assert "not found" in r["error"].lower()
    print("  cli command-not-found degrades gracefully OK")


def test_cli_in_providers():
    assert "cli" in PROVIDERS
    print("  'cli' registered in PROVIDERS OK")


if __name__ == "__main__":
    print("=== cli worker provider tests ===")
    test_cli_in_providers()
    test_cli_stdin()
    test_cli_prompt_substitution()
    test_cli_missing_command()
    test_cli_command_not_found()
    print("ALL CLI PROVIDER TESTS PASSED")
