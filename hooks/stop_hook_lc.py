#!/usr/bin/env python3
"""Stop hook — delegates to session_graph finalize_session node."""
import sys
from pathlib import Path

_PROJECT_ROOT = Path.home() / "workspace/claude-hooks"
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.logger import flush_logs
from sqlite_log_handler import setup
from utils import read_stdin, write_json_to_stdout

log = setup("stop_hook_lc")


def _run(hook_input: dict) -> dict:
    session_id = hook_input.get("session_id", "")
    if not session_id:
        return {}

    from langchain_learning.session_graph import run_stop
    run_stop(session_id=session_id)
    return {}


def _run_safe(hook_input: dict) -> dict:
    try:
        return _run(hook_input)
    except Exception as e:
        log.error("stop_hook_lc failed: %s", e)
        return {}


def main():
    _run_safe(read_stdin())
    write_json_to_stdout()
    flush_logs()


if __name__ == "__main__":
    main()
