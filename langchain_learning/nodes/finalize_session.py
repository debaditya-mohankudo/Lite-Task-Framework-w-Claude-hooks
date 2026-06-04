"""FinalizeSessionNode — cleans keywords and persists stop state."""
from __future__ import annotations

from pathlib import Path

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class FinalizeSessionNode:
    """Aggregate keywords, filter stopwords, persist stop state to sessions.db.

    Also clears prompt_id_tmp so the next session starts fresh.
    Skips gracefully if session has turn=0 (never got a real prompt).
    """

    def __call__(self, state: SessionState) -> dict:
        from langchain_learning import session_graph as sg
        from src.config import config as _src_cfg
        from core.db.session_db import SessionDB
        from core.stopwords import filter_keywords

        entry("finalize_session", state)

        session_id    = state.get("session_id", "")
        prompt_id_tmp = _src_cfg.prompt_id_tmp

        if not session_id:
            return {}

        sessions_db = sg._SESSIONS_DB or Path.home() / ".claude" / "sessions.db"
        if not sessions_db.exists():
            return {}

        db    = SessionDB.open(sessions_db)
        saved = db.get(session_id)
        if not saved or saved.get("turn", 0) == 0:
            _log.info("[finalize_session] session=%s turn=0, skipping", session_id[:8])
            return {}

        raw_keywords   = set(saved.get("keywords", []))
        clean_keywords = filter_keywords(raw_keywords)

        db.upsert(session_id, {
            **saved,
            "keywords":      clean_keywords,
            "current_state": "stop",
        })
        _log.info("[finalize_session] session=%s raw_kw=%d clean_kw=%d",
                  session_id[:8], len(raw_keywords), len(clean_keywords))

        if prompt_id_tmp.exists():
            prompt_id_tmp.unlink()
            _log.info("[finalize_session] prompt_id cleared")

        return {}
