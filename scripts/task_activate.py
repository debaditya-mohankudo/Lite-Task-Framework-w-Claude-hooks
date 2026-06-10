#!/usr/bin/env python3
"""CLI entry point for task_graph operations — runs in the claude-hooks venv where langgraph lives.

Usage:
    uv run python scripts/task_activate.py activate <task_id> <session_id>
    uv run python scripts/task_activate.py clear <session_id>
    uv run python scripts/task_activate.py pop <session_id>

Prints a JSON result to stdout. Exit code 1 on error.
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
for _p in [str(_ROOT), str(_ROOT / "hooks"), str(_ROOT / "src")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from langchain_learning.task_graph import run_task_activate, run_clear_active, run_task_pop, run_add_decision


def main() -> None:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: task_activate.py activate <task_id> <session_id> | clear <session_id> | pop <session_id> | decision <task_id> <session_id> <text>"}))
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "activate":
        if len(sys.argv) != 4:
            print(json.dumps({"error": "activate requires <task_id> <session_id>"}))
            sys.exit(1)
        result = run_task_activate(task_id=sys.argv[2], session_id=sys.argv[3])

    elif cmd == "clear":
        if len(sys.argv) != 3:
            print(json.dumps({"error": "clear requires <session_id>"}))
            sys.exit(1)
        result = run_clear_active(session_id=sys.argv[2])

    elif cmd == "pop":
        if len(sys.argv) != 3:
            print(json.dumps({"error": "pop requires <session_id>"}))
            sys.exit(1)
        result = run_task_pop(session_id=sys.argv[2])

    elif cmd == "decision":
        if len(sys.argv) < 5:
            print(json.dumps({"error": "decision requires <task_id> <session_id> <text>"}))
            sys.exit(1)
        decision_text = " ".join(sys.argv[4:])
        result = run_add_decision(task_id=sys.argv[2], session_id=sys.argv[3], decision=decision_text)

    else:
        print(json.dumps({"error": f"unknown command {cmd!r}"}))
        sys.exit(1)

    print(json.dumps(result))


if __name__ == "__main__":
    main()
