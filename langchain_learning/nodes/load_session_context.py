"""LoadSessionContextNode — retrieves top-2 session summaries by keyword score."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class LoadSessionContextNode:
    """Keyword-search session_summaries and return top-2 as a formatted string.

    Tags are weighted 3×, body 1×. Result injected as ## Session context.
    """

    def __call__(self, state: SessionState) -> dict:
        from langchain_learning import session_graph as sg
        entry("load_session_context", state, keywords=len(state.get("keywords") or []))

        keywords = set(state.get("keywords") or [])
        if not keywords:
            return {"session_context": "", "session_context_ids": []}

        sessions_db = sg._SESSIONS_DB or Path.home() / ".claude" / "sessions.db"
        if not sessions_db.exists():
            return {"session_context": "", "session_context_ids": []}

        try:
            with sqlite3.connect(f"file:{sessions_db}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT session_id, summary, tags FROM session_summaries"
                ).fetchall()
        except Exception as exc:
            _log.error("[load_session_context] DB error: %s", exc)
            return {"session_context": "", "session_context_ids": []}

        def _score(row) -> int:
            tag_hits  = sum(3 for t in (row["tags"] or "").split(",") if t.strip() in keywords)
            body_hits = sum(1 for w in row["summary"].lower().split() if w.strip(".,;:") in keywords)
            return tag_hits + body_hits

        scored = sorted(rows, key=_score, reverse=True)
        top2   = [r for r in scored[:2] if _score(r) > 0]
        if not top2:
            _log.info("[load_session_context] no matching summaries")
            return {"session_context": "", "session_context_ids": []}

        lines, ids = [], []
        for r in top2:
            tag_hint = ", ".join(t.strip() for t in (r["tags"] or "").split(",") if t.strip())[:80]
            preview  = (r["summary"] or "")[:200]
            lines.append(f"- [{r['session_id'][:8]}] ({tag_hint}): {preview}")
            ids.append(r["session_id"])

        _log.info("[load_session_context] injecting ids=%s", [i[:8] for i in ids])
        return {"session_context": "\n".join(lines), "session_context_ids": ids}
