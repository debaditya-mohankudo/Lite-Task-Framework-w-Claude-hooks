#!/usr/bin/env python3
"""PostToolUse hook — delegates to session_graph log_tool_usage node."""
import sys
from pathlib import Path

_PROJECT_ROOT = Path.home() / "workspace/claude-hooks"
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.config import config as _cfg
from langchain_learning.config import config as _lc_cfg
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
    tool_response = hook_input.get("tool_response") or {}
    log.debug("tool_response raw: %r", tool_response)
    if not isinstance(tool_response, dict):
        tool_response = {"raw": str(tool_response)}
    # Claude Code wraps MCP responses: {"content": [{"type": "text", "text": "<json>"}]}
    # Unwrap to the inner dict so _result_found() can inspect actual fields
    if "content" in tool_response and isinstance(tool_response.get("content"), list):
        try:
            import json as _json
            text = tool_response["content"][0].get("text", "")
            parsed = _json.loads(text)
            if isinstance(parsed, dict):
                tool_response = parsed
        except Exception:
            pass
    # tool_use_id = hook_input.get("tool_use_id", "") or os.environ.get("ANTHROPIC_TOOL_USE_ID", "")

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
        tool_result=tool_response,
        session_id=session_id,
        duration_ms=duration_ms,
        prompt=prompt,
    )

    return {}


def _run_safe(hook_input: dict) -> dict:
    try:
        return _run(hook_input)
    except Exception as e:
        log.error("tool_usage_logger_lc failed: %s", e)
        raise


def main():
    try:
        _run_safe(read_stdin())
        write_json_to_stdout()
    except Exception as e:
        write_json_to_stdout(error=f"tool_usage_logger_lc failed: {e}")
        flush_logs()
        if _lc_cfg.dev_mode:
            sys.exit(2)
    finally:
        flush_logs()


if __name__ == "__main__":
    main()
