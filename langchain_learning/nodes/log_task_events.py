"""LogTaskEventsNode — appends a turn event to the active task at session stop."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_TASKS_DB   = Path.home() / ".claude" / "proj_tasks.db"
_PROMPT_TMP = Path.home() / ".claude" / "current_prompt_text.tmp"

_MAX_SUMMARY = 200


def _prompt_summary() -> str:
    try:
        return _PROMPT_TMP.read_text().strip()[:_MAX_SUMMARY]
    except Exception:
        return ""


class LogTaskEventsNode:
    """Write one task_event row for the active task at session stop.

    Reads active_task_id directly from checkpoint state — set explicitly by
    tasks__set_active, never guessed. No-ops silently when no task is active.
    """

    def __call__(self, state: SessionState) -> dict:
        entry("log_task_events", state)

        task_id = state.get("active_task_id", "")
        if not task_id:
            _log.info("[log_task_events] no active task — skipping")
            return {}

        if not _TASKS_DB.exists():
            _log.warning("[log_task_events] proj_tasks.db not found")
            return {}

        prompt_id  = state.get("prompt_id", "")
        session_id = state.get("session_id", "")
        turn       = state.get("turn", 0)
        tools_raw  = state.get("prompt_tools") or []
        tools_str  = ",".join(str(t) for t in tools_raw)
        summary    = _prompt_summary()

        try:
            with sqlite3.connect(str(_TASKS_DB), timeout=5) as conn:
                conn.execute(
                    """INSERT INTO task_events
                       (task_id, prompt_id, session_id, turn, summary, tools)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (task_id, prompt_id, session_id, turn, summary, tools_str),
                )
                conn.execute(
                    "UPDATE open_tasks SET updated_at=datetime('now') WHERE id=?",
                    (task_id,),
                )
            _log.info("[log_task_events] logged task=%s turn=%d tools=%s", task_id, turn, tools_str)
        except Exception as exc:
            _log.error("[log_task_events] DB error: %s", exc)

        return {}
