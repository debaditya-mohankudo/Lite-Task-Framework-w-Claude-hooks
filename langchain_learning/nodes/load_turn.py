"""LoadTurnNode — reads current turn from sessions.db."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class LoadTurnNode:
    """Read the current turn counter from sessions.db.

    Allows callers to always pass turn=0; the graph self-corrects before
    persist_session increments it.
    """

    def __call__(self, state: SessionState) -> dict:
        from langchain_learning import session_graph as sg
        entry("load_turn", state)

        session_id = state.get("session_id", "")
        if not session_id:
            return {}

        sessions_db = sg._SESSIONS_DB or Path.home() / ".claude" / "sessions.db"
        if not sessions_db.exists():
            return {}

        try:
            with sqlite3.connect(f"file:{sessions_db}?mode=ro", uri=True) as conn:
                row = conn.execute(
                    "SELECT turn FROM sessions WHERE session_id = ?", (session_id,)
                ).fetchone()
            if row:
                _log.info("[load_turn] session=%s turn=%d", session_id[:8], row[0])
                return {"turn": row[0]}
        except Exception as exc:
            _log.warning("[load_turn] DB error: %s", exc)

        return {}
