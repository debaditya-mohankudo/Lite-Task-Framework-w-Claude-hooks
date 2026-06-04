#!/usr/bin/env python3
"""
PreToolUse hook — gate check for MCP tool calls.

Parses hook input → runs gate check → emits allow/deny.
Gate policy lives in gates.py. Fail-open: any error lets the tool proceed.
"""
import sys
from pathlib import Path

_PROJECT_ROOT = Path.home() / "workspace/claude-hooks"
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.config import config as _cfg
from src.logger import flush_logs
from sqlite_log_handler import setup
from utils import read_stdin, write_json_to_stdout
from core.tool_registry import strip_mcp_prefix

log = setup("pre_tool_use_lc")


def _run(hook_input: dict) -> dict:
    tool_name  = hook_input.get("tool_name", "")
    session_id = hook_input.get("session_id", "")

    if not tool_name or not session_id or not tool_name.startswith("mcp__"):
        return {}

    short_name = strip_mcp_prefix(tool_name)
    if not short_name or short_name.startswith("memory__"):
        return {}

    from langchain_learning.session_graph import run_gate

    result = run_gate(
        tool_name=short_name,
        tool_input=hook_input.get("tool_input") or {},
        session_id=session_id,
    )

    if result["gate_denied"]:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": result["gate_reason"],
            }
        }

    return {}


def _run_safe(hook_input: dict) -> dict:
    try:
        return _run(hook_input)
    except Exception as e:
        log.error("pre_tool_use_lc failed: %s", e)
        return {}  # fail-open


def main():
    result = _run_safe(read_stdin())
    write_json_to_stdout(result if result else None)
    flush_logs()


if __name__ == "__main__":
    main()
