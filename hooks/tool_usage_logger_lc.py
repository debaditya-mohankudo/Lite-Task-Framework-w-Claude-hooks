#!/usr/bin/env python3
"""PostToolUse hook — delegates to session_graph log_tool_usage node."""
import os
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

log = setup("tool_usage_logger_lc")


def _run(hook_input: dict) -> dict:
    tool_name   = hook_input.get("tool_name", "")
    session_id  = hook_input.get("session_id", "")
    duration_ms = float(hook_input.get("duration_ms", 0))
    tool_input  = hook_input.get("tool_input", {})
    tool_use_id = hook_input.get("tool_use_id", "") or os.environ.get("ANTHROPIC_TOOL_USE_ID", "")

    from core.db.session_db import SessionDB
    import langchain_learning.session_graph as _sg
    sessions_db = _sg._SESSIONS_DB or Path.home() / ".claude" / "sessions.db"
    prompt_id = SessionDB.open(sessions_db).get_prompt_id(session_id) if (session_id and sessions_db.exists()) else ""

    if not tool_name or not tool_name.startswith("mcp__"):
        return {}

    short_name = strip_mcp_prefix(tool_name) or tool_name
    if short_name.startswith("memory__"):
        return {}

    _prompt_tmp = Path.home() / ".claude" / "current_prompt_text.tmp"
    prompt = _prompt_tmp.read_text().strip() if _prompt_tmp.exists() else ""

    from langchain_learning.session_graph import run_post_tool
    run_post_tool(
        tool_name=short_name,
        tool_input=tool_input if isinstance(tool_input, dict) else {},
        session_id=session_id,
        prompt_id=prompt_id,
        tool_use_id=tool_use_id,
        duration_ms=duration_ms,
        prompt=prompt,
    )

    return {}


def _run_safe(hook_input: dict) -> dict:
    try:
        return _run(hook_input)
    except Exception as e:
        log.error("tool_usage_logger_lc failed: %s", e)
        return {}


def main():
    _run_safe(read_stdin())
    write_json_to_stdout()
    flush_logs()


if __name__ == "__main__":
    main()
