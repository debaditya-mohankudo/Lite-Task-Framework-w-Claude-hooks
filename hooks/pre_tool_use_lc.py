#!/usr/bin/env python3
"""
PreToolUse hook — in-process, no HTTP.

Thin wrapper: parse hook input → check gates → record tool call → emit allow/deny.
Gate policy lives in gates.py. Session persistence in server/core/db/session_db.py.

Fail-open: any error lets the tool proceed — the gate is a safeguard, not a
single point of failure for all tool use.
"""
import json
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from hooks_config import cfg as _cfg
_SESSIONS_DB = _cfg.sessions_db
from sqlite_log_handler import setup
from utils import read_stdin, write_json_to_stdout
from gates import check as gate_check

from core.tool_registry import strip_mcp_prefix
from core.db.session_db import SessionDB

log = setup("pre_tool_use_lc")


def _emit_deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }), file=sys.stdout)


def main():
    try:
        hook_input = read_stdin()
        tool_name  = hook_input.get("tool_name", "")
        session_id = hook_input.get("session_id", "")
        prompt_id  = hook_input.get("tool_use_id", "") or hook_input.get("prompt_id", "")

        if not tool_name or not session_id or not tool_name.startswith("mcp__"):
            log.debug("pre_tool_use: skipping non-MCP tool=%r session=%r", tool_name, session_id)
            write_json_to_stdout()
            return

        short_name = strip_mcp_prefix(tool_name)
        if not short_name or short_name.startswith("memory__"):
            log.debug("pre_tool_use: skipping memory tool=%r", tool_name)
            write_json_to_stdout()
            return

        db = SessionDB.open(_SESSIONS_DB)
        deny, reason = gate_check(short_name, lambda prereq: db.prompt_had_tool(prompt_id, prereq))

        if deny:
            log.warning("DENY %s (prompt_id=%s): %s", short_name, prompt_id, reason)
            _emit_deny(reason)
            return

        log.debug("ALLOW %s (prompt_id=%s)", short_name, prompt_id)
        write_json_to_stdout()

    except Exception as e:
        log.error("pre_tool_use_lc failed: %s", e)
        write_json_to_stdout()  # fail-open


if __name__ == "__main__":
    main()
