"""PersistSessionNode — writes final session snapshot to sessions.db (Stop chain only)."""
from __future__ import annotations

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class PersistSessionNode:
    """Write session snapshot (keywords, domains, turn, state) to sessions.db via SessionDB.

    Called only from finalize_session (Stop chain). Session data is written once,
    when the session is complete — not mid-turn. Single responsibility: upsert.
    """

    def __call__(self, state: SessionState) -> dict:
        from langchain_learning import session_graph as sg
        from core.db.session_db import SessionDB
        from pathlib import Path

        entry("persist_session", state,
              domains=state.get("domains"),
              keywords=len(state.get("keywords", [])))

        session_id = state.get("session_id", "")
        if not session_id:
            return {}

        sessions_db = sg._SESSIONS_DB or Path.home() / ".claude" / "sessions.db"
        if not sessions_db.exists():
            return {}

        try:
            db = SessionDB.open(sessions_db)
            saved = db.get(session_id) or {}
            db.upsert(session_id, {
                **saved,
                "keywords":       set(state.get("keywords", [])),
                "domains":        set(state.get("domains", [])),
                "current_state":  state.get("current_state", saved.get("current_state", "stop")),
                "state_history":  saved.get("state_history", []),
                "injected_names": set(saved.get("injected_names", [])),
                "tasks":          saved.get("tasks", []),
                "turn":           state.get("turn", saved.get("turn", 0)),
            })
            _log.info("[persist_session] session=%s turn=%d", session_id[:8], state.get("turn", 0))
        except Exception as exc:
            _log.error("[persist_session] DB error: %s", exc)

        return {}
