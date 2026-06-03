"""
Minimal Claude subagent via `claude -p` (no API key needed).

Spawns a full Claude Code process, so ALL hooks in ~/.claude/settings.json fire:
  - UserPromptSubmit → memory_loader_lc.py injects memories + tool hints into subagent context
  - PreToolUse       → pre_tool_use_lc.py gates any tool calls the subagent makes
  - Stop             → stop_hook_lc.py persists subagent session keywords/domains

Use subagent_with_no_context for a raw call with no hooks or MCP servers:
    --settings /tmp/no-hooks-settings.json (empty JSON) overrides hook config.

Usage:
    uv run python langchain_learning/subagent.py "summarize the LCEL pipeline"
"""
import json
import subprocess
import sys
from pathlib import Path

_NO_HOOKS_SETTINGS = Path("/tmp/no-hooks-settings.json")
if not _NO_HOOKS_SETTINGS.exists():
    _NO_HOOKS_SETTINGS.write_text(json.dumps({}))

from langchain_core.runnables import RunnableLambda


def call_claude(prompt: str, system: str = "") -> str:
    cmd = ["claude", "-p", prompt]
    if system:
        cmd += ["--system", system]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"claude exited {result.returncode}: {result.stderr.strip()}")
    return result.stdout.strip()


def call_claude_no_context(prompt: str, system: str = "") -> str:
    """Raw Claude call with no hooks, no memory injection, no MCP servers.

    Passes --settings /tmp/no-hooks-settings.json (empty config) so no hooks
    or MCP servers are loaded. Auth credentials from ~/.claude are still used.
    """
    cmd = ["claude", "-p", prompt, "--settings", "/tmp/no-hooks-settings.json"]
    if system:
        cmd += ["--system", system]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"claude exited {result.returncode}: {result.stderr.strip()}")
    return result.stdout.strip()


def _invoke_subagent(x: dict) -> str:
    return call_claude(x["input"], x.get("system", ""))


def _invoke_subagent_no_context(x: dict) -> str:
    return call_claude_no_context(x["input"], x.get("system", ""))


subagent = RunnableLambda(_invoke_subagent)

subagent_with_no_context = RunnableLambda(_invoke_subagent_no_context)


if __name__ == "__main__":
    prompt = " ".join(sys.argv[1:]) or "say hello"
    print(subagent.invoke({"input": prompt}))
