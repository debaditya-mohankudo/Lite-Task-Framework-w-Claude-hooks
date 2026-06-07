"""LoadTaskHistoryNode — injects turn summaries for the active task, current session only."""
from __future__ import annotations

import sqlite3

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class LoadTaskHistoryNode:
    """Read task_events for active_task_id scoped to the current session only.

    Returns all events for (task_id, session_id) ordered oldest-first.
    Cross-session context comes from LoadTaskCommitsNode (last 5 git commits).

    Tags: task-history, task-events, session-scoped, task-context
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_task_history", state)

        task_id    = state.get("active_task_id", "")
        session_id = state.get("session_id", "")

        if not task_id or not session_id:
            return {"task_context": []}

        if not _cfg.tasks_db.exists():
            _log.warning("[load_task_history] tasks_db not found")
            return {"task_context": []}

        try:
            conn = sqlite3.connect(f"file:{_cfg.tasks_db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT turn, summary, tools, session_id FROM task_events
                   WHERE task_id = ? AND session_id = ?
                   ORDER BY turn ASC""",
                (task_id, session_id),
            ).fetchall()
            conn.close()
        except Exception as exc:
            _log.error("[load_task_history] DB error: %s", exc)
            return {"task_context": []}

        task_context = [dict(r) for r in rows]
        _log.info("[load_task_history] task=%s turns=%d session=%s", task_id, len(task_context), session_id[:8])
        return {"task_context": task_context}
