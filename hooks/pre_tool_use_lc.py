#!/usr/bin/env python3
"""
PreToolUse hook — LCEL pipeline variant.

Thin entry point: parse stdin → invoke gate pipeline → emit allow/deny.

Gate policy lives in gates.py.
Pipeline shape defined in langchain_learning/gate_pipeline.py.

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
from sqlite_log_handler import setup
from utils import read_stdin, write_json_to_stdout
from gates import check as gate_check

from core.tool_registry import strip_mcp_prefix
from core.db.session_db import SessionDB
from langchain_learning.gate_pipeline import build_pre_tool_pipeline

log = setup("pre_tool_use_lc")

# Exposed as a module-level variable so tests can patch it without rebuilding
# the pipeline — the getter lambda reads it at each .invoke() call.
_SESSIONS_DB = _cfg.sessions_db

_pipeline = build_pre_tool_pipeline(
    cfg=_cfg,
    strip_mcp_prefix_fn=strip_mcp_prefix,
    gate_check_fn=gate_check,
    SessionDB=SessionDB,
    sessions_db_getter=lambda: _SESSIONS_DB,
)


def _run_safe(hook_input: dict) -> dict:
    try:
        return _pipeline.invoke(hook_input)
    except Exception as e:
        log.error("pre_tool_use_lc failed: %s", e)
        return {}  # fail-open


hook = RunnableLambda(_run_safe)


def main():
    result = hook.invoke(read_stdin())
    write_json_to_stdout(result if result else None)


if __name__ == "__main__":
    main()
