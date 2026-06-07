"""LoadTaskHistoryNode — injects prior turn summaries for the active task with hybrid session/cross-session scoping."""
from __future__ import annotations

import sqlite3

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_MAX_TURNS = 10


class LoadTaskHistoryNode:
    """Read task_events for active_task_id with hybrid scoping.

    If the current session already has ≥ _MAX_TURNS events for this task, scope
    to the current session only (all rows, no limit) — they're all related.
    Otherwise fall back to the last _MAX_TURNS events across all sessions so
    cross-session history fills the gap when resuming a task in a new session.
    Returns events ordered oldest-first for readability.
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

            session_count = conn.execute(
                "SELECT COUNT(*) FROM task_events WHERE task_id = ? AND session_id = ?",
                (task_id, session_id),
            ).fetchone()[0]

            if session_count >= _MAX_TURNS:
                # current session has enough context — stay scoped to it
                rows = conn.execute(
                    """SELECT turn, summary, tools, session_id FROM task_events
                       WHERE task_id = ? AND session_id = ?
                       ORDER BY turn ASC""",
                    (task_id, session_id),
                ).fetchall()
                scope = "session"
            else:
                # not enough session turns — pull last N across all sessions
                rows = conn.execute(
                    """SELECT turn, summary, tools, session_id FROM task_events
                       WHERE task_id = ?
                       ORDER BY rowid DESC
                       LIMIT ?""",
                    (task_id, _MAX_TURNS),
                ).fetchall()
                rows = list(reversed(rows))
                scope = "global"

            conn.close()
        except Exception as exc:
            _log.error("[load_task_history] DB error: %s", exc)
            return {"task_context": []}

        task_context = [dict(r) for r in rows]
        _log.info("[load_task_history] task=%s turns=%d scope=%s", task_id, len(task_context), scope)
        return {"task_context": task_context}
