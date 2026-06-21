"""LoadTaskHistoryNode — injects turn summaries for the active task across all sessions."""
from __future__ import annotations

import sqlite3

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


_HISTORY_LIMIT = 20


class LoadTaskHistoryNode:
    """Read task_events for active_task_id across all sessions, last _HISTORY_LIMIT turns oldest-first.

    session_id is included in each row so the injector can show session boundaries.

    Tags: task-history, task-events, cross-session, task-context
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_task_history", state)

        task_id = state.get("active_task_id", "")

        if not task_id:
            _log.info("[load_task_history] no active task — skipped")
            return {"task_context": []}

        if not _cfg.tasks_db.exists():
            _log.warning("[load_task_history] tasks_db not found")
            return {"task_context": []}

        try:
            conn = sqlite3.connect(f"file:{_cfg.tasks_db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT turn, summary, tools, session_id FROM task_events
                   WHERE task_id = ?
                   ORDER BY id ASC
                   LIMIT ?""",
                (task_id, _HISTORY_LIMIT),
            ).fetchall()
            conn.close()
        except Exception as exc:
            _log.error("[load_task_history] DB error: %s", exc)
            return {"task_context": []}

        task_context = [dict(r) for r in rows]
        session_id = state.get("session_id", "")
        _log.info("[load_task_history] task=%s turns=%d session=%s", task_id, len(task_context), session_id[:8])
        return {"task_context": task_context}
