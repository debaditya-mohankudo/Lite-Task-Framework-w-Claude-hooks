#!/usr/bin/env python3
"""
PreToolUse hook — in-process, no HTTP.

Thin wrapper: parse hook input → check gates → record tool call → emit allow/deny.
Gate policy lives in gates.py. Session persistence in server/core/db/session_db.py.

Fail-open: any error lets the tool proceed — the gate is a safeguard, not a
single point of failure for all tool use.
"""
import sys
from pathlib import Path

_PROJECT_ROOT = Path.home() / "workspace/claude-hooks"
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from langchain_core.runnables import RunnableLambda

from src.config import config as _cfg
_SESSIONS_DB = _cfg.sessions_db
from sqlite_log_handler import setup
from utils import read_stdin, write_json_to_stdout
from gates import check as gate_check

from core.tool_registry import strip_mcp_prefix
from core.db.session_db import SessionDB

log = setup("pre_tool_use_lc")


def _run(hook_input: dict) -> dict:
    tool_name  = hook_input.get("tool_name", "")
    session_id = hook_input.get("session_id", "")
    prompt_id_tmp = _cfg.prompt_id_tmp
    prompt_id  = (prompt_id_tmp.read_text().strip() if prompt_id_tmp.exists() else "") \
                 or hook_input.get("tool_use_id", "") or hook_input.get("prompt_id", "")

    if not tool_name or not session_id or not tool_name.startswith("mcp__"):
        log.debug("pre_tool_use: skipping non-MCP tool=%r session=%r", tool_name, session_id)
        return {}

    short_name = strip_mcp_prefix(tool_name)
    if not short_name or short_name.startswith("memory__"):
        log.debug("pre_tool_use: skipping memory tool=%r", tool_name)
        return {}

    db = SessionDB.open(_SESSIONS_DB)
    deny, reason = gate_check(short_name, lambda prereq: db.prompt_had_tool(prompt_id, prereq))

    if deny:
        log.warning("DENY %s (prompt_id=%s): %s", short_name, prompt_id, reason)
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }

    log.info("ALLOW %s (prompt_id=%s)", short_name, prompt_id)
    return {}


def _run_safe(hook_input: dict) -> dict:
    try:
        return _run(hook_input)
    except Exception as e:
        log.error("pre_tool_use_lc failed: %s", e)
        return {}  # fail-open


hook = RunnableLambda(_run_safe)


def main():
    result = hook.invoke(read_stdin())
    write_json_to_stdout(result if result else None)


if __name__ == "__main__":
    main()
