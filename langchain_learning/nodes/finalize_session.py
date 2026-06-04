"""FinalizeSessionNode — loads session, filters stopwords, sets stop state."""
from __future__ import annotations

from pathlib import Path

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class FinalizeSessionNode:
    """Load session from DB, filter stopwords from keywords, set current_state=stop.

    Writes clean keywords, domains, turn, and current_state into SessionState.
    The actual DB write is delegated to PersistSessionNode which runs next in the
    Stop chain — single responsibility: compute the final session snapshot.

    Skips gracefully if session has turn=0 (never got a real prompt).
    """

    def __call__(self, state: SessionState) -> dict:
        from langchain_learning import session_graph as sg
        from core.db.session_db import SessionDB
        from core.stopwords import filter_keywords

        entry("finalize_session", state)

        session_id  = state.get("session_id", "")

        if not session_id:
            return {"skip_persist": True}

        sessions_db = sg._SESSIONS_DB or Path.home() / ".claude" / "sessions.db"
        if not sessions_db.exists():
            return {"skip_persist": True}

        db    = SessionDB.open(sessions_db)
        saved = db.get(session_id)
        if not saved or saved.get("turn", 0) == 0:
            _log.info("[finalize_session] session=%s turn=0, skipping", session_id[:8])
            return {"skip_persist": True}

        raw_keywords   = set(saved.get("keywords", []))
        clean_keywords = filter_keywords(raw_keywords)

        _log.info("[finalize_session] session=%s raw_kw=%d clean_kw=%d",
                  session_id[:8], len(raw_keywords), len(clean_keywords))

        return {
            "keywords":      sorted(clean_keywords),
            "domains":       saved.get("domains", []),
            "turn":          saved.get("turn", 0),
            "current_state": "stop",
            "skip_persist":  False,
        }
