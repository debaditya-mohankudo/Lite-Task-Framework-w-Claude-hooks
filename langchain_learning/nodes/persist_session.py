"""PersistSessionNode — writes session snapshot to sessions.db."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class PersistSessionNode:
    """Write session state snapshot to sessions.db (upsert by session_id).

    Increments turn by 1. Does NOT write session_summaries — that is the
    job of the session-compact-persist skill.
    """

    def __call__(self, state: SessionState) -> dict:
        from langchain_learning import session_graph as sg
        entry("persist_session", state,
              domains=state.get("domains"),
              keywords=len(state.get("keywords", [])))

        session_id = state["session_id"]
        if not session_id:
            return {"turn": state["turn"] + 1}

        sessions_db = sg._SESSIONS_DB or Path.home() / ".claude" / "sessions.db"
        if not sessions_db.exists():
            return {"turn": state["turn"] + 1}

        new_turn = state["turn"] + 1
        try:
            with sqlite3.connect(str(sessions_db)) as conn:
                existing = conn.execute(
                    "SELECT session_id FROM sessions WHERE session_id = ?", (session_id,)
                ).fetchone()
                domains_json  = json.dumps(state["domains"])
                keywords_json = json.dumps(state["keywords"])
                if existing:
                    conn.execute(
                        "UPDATE sessions SET keywords=?, domains=?, turn=?, updated_at=datetime('now') WHERE session_id=?",
                        (keywords_json, domains_json, new_turn, session_id),
                    )
                else:
                    conn.execute(
                        "INSERT INTO sessions (session_id, keywords, domains, turn, updated_at) VALUES (?, ?, ?, ?, datetime('now'))",
                        (session_id, keywords_json, domains_json, new_turn),
                    )
                conn.commit()
        except Exception as exc:
            _log.error("[persist_session] DB error: %s", exc)

        _log.info("[persist_session] session=%s new_turn=%d", session_id[:8], new_turn)
        return {"turn": new_turn}
