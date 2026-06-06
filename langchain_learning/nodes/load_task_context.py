"""LoadTaskContextNode — injects prior turn summaries for the active task (current session only)."""
from __future__ import annotations

import sqlite3

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_MAX_TURNS = 10


class LoadTaskContextNode:
    """Read task_events for active_task_id + session_id and store as task_context.

    Only current-session events are included — cross-session history is excluded
    to keep context tight and relevant to the ongoing work.
    Returns top _MAX_TURNS events ordered by turn ascending.
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_task_context", state)

        task_id    = state.get("active_task_id", "")
        session_id = state.get("session_id", "")

        if not task_id or not session_id:
            return {"task_context": []}

        if not _cfg.tasks_db.exists():
            _log.warning("[load_task_context] tasks_db not found")
            return {"task_context": []}

        try:
            conn = sqlite3.connect(f"file:{_cfg.tasks_db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT turn, summary, tools FROM task_events
                   WHERE task_id = ? AND session_id = ?
                   ORDER BY turn ASC
                   LIMIT ?""",
                (task_id, session_id, _MAX_TURNS),
            ).fetchall()
            conn.close()
        except Exception as exc:
            _log.error("[load_task_context] DB error: %s", exc)
            return {"task_context": []}

        task_context = [dict(r) for r in rows]
        _log.info("[load_task_context] task=%s session=%s turns=%d",
                  task_id, session_id[:8], len(task_context))
        return {"task_context": task_context}
