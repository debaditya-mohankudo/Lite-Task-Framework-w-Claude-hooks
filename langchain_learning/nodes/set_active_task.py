"""SetActiveTaskNode — writes active_task_id + title into state from proj_tasks.db."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_TASKS_DB = Path.home() / ".claude" / "proj_tasks.db"


class SetActiveTaskNode:
    """Look up task by id, write active_task_id + active_task_title into state.

    Also marks the task wip if it is currently open.
    Fails gracefully — if task not found, active_task_id stays empty.
    """

    def __call__(self, state: SessionState) -> dict:
        entry("set_active_task", state)

        task_id = state.get("active_task_id", "")
        if not task_id:
            _log.warning("[set_active_task] no active_task_id in state")
            return {}

        if not _TASKS_DB.exists():
            _log.warning("[set_active_task] proj_tasks.db not found")
            return {"active_task_id": "", "active_task_title": ""}

        try:
            with sqlite3.connect(str(_TASKS_DB), timeout=5) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT id, title, status FROM open_tasks WHERE id = ?", (task_id,)
                ).fetchone()
                if row is None:
                    _log.warning("[set_active_task] task_id=%s not found", task_id)
                    return {"active_task_id": "", "active_task_title": ""}
                if row["status"] == "open":
                    conn.execute(
                        "UPDATE open_tasks SET status='wip', updated_at=datetime('now') WHERE id=?",
                        (task_id,),
                    )
        except Exception as exc:
            _log.error("[set_active_task] DB error: %s", exc)
            return {"active_task_id": "", "active_task_title": ""}

        _log.info("[set_active_task] activated task=%s title=%r", task_id, row["title"])
        return {"active_task_id": task_id, "active_task_title": row["title"]}
