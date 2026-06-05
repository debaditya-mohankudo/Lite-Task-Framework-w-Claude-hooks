"""LoadActiveTaskNode — reads active_session_task for this session into state."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_TASKS_DB = Path.home() / ".claude" / "proj_tasks.db"


class LoadActiveTaskNode:
    """Read active_session_task for the current session_id from proj_tasks.db.

    Sets active_task_id in state. Called at the top of the UPS chain so the
    active task is available for the rest of the turn (injection, logging).
    No scoring, no keyword matching — purely deterministic lookup by session_id.
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_active_task", state)

        session_id = state.get("session_id", "")
        if not session_id or not _TASKS_DB.exists():
            return {"active_task_id": ""}

        try:
            with sqlite3.connect(f"file:{_TASKS_DB}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT task_id FROM active_session_task WHERE session_id = ?",
                    (session_id,),
                ).fetchone()
        except Exception as exc:
            _log.error("[load_active_task] DB error: %s", exc)
            return {"active_task_id": ""}

        task_id = row["task_id"] if row else ""
        if task_id:
            _log.info("[load_active_task] session=%s active_task=%s", session_id[:8], task_id)
        return {"active_task_id": task_id}
