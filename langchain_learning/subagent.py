"""
Minimal Claude subagent via `claude -p` — no API key needed, no hooks, no session persistence.

Flags used:
  --no-session-persistence  skip writing session to disk
  --output-format json      structured output, result parsed from JSON
  --settings {}             empty config suppresses hooks and MCP servers
  --model haiku             cheapest/fastest for subagent use

Note: --bare skips auth too, so we use --settings instead to suppress hooks.

Usage:
    uv run python langchain_learning/subagent.py "summarize the LCEL pipeline"
"""
import json
import subprocess
import sys
from pathlib import Path

from langchain_core.runnables import RunnableLambda

_NO_HOOKS_SETTINGS = Path("/tmp/no-hooks-settings.json")
if not _NO_HOOKS_SETTINGS.exists():
    _NO_HOOKS_SETTINGS.write_text(json.dumps({}))


def call_claude_no_context(prompt: str, system: str = "", model: str = "claude-haiku-4-5-20251001") -> str:
    cmd = [
        "claude", "-p", prompt,
        "--no-session-persistence",
        "--output-format", "json",
        "--settings", str(_NO_HOOKS_SETTINGS),
        "--model", model,
    ]
    if system:
        cmd += ["--system-prompt", system]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"claude exited {result.returncode}: {result.stderr.strip()}")
    data = json.loads(result.stdout)
    if data.get("is_error"):
        raise RuntimeError(f"claude error: {data['result']}")
    return data["result"]


def _invoke(x: dict) -> str:
    return call_claude_no_context(x["input"], x.get("system", ""), x.get("model", "claude-haiku-4-5-20251001"))


subagent = RunnableLambda(_invoke)


if __name__ == "__main__":
    prompt = " ".join(sys.argv[1:]) or "say hello"
    print(subagent.invoke({"input": prompt}))
