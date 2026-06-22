"""ScoreToolsNode — retrieves relevant tool hints from tool_hints.sqlite."""
from __future__ import annotations

import sqlite3

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class ScoreToolsNode:
    """Retrieve top-5 tool hints by domain match + keyword overlap.

    Skipped entirely when classify_domain sets skip_tools=True.
    """

    def __call__(self, state: SessionState) -> dict:
        entry("score_tools", state, domains=state.get("domains"))

        domains  = set(state["domains"])
        keywords = set(state["keywords"])

        if not _cfg.tool_hints_db.exists():
            _log.warning("[score_tools] tool_hints.sqlite not found at %s", _cfg.tool_hints_db)
            return {"tool_hints": []}

        try:
            conn = sqlite3.connect(f"file:{_cfg.tool_hints_db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT tool_name, domain, skill, count, keywords FROM mcp_tool_hints"
            ).fetchall()
            conn.close()
        except Exception as exc:
            _log.error("[score_tools] DB error: %s", exc)
            return {"tool_hints": []}

        scored: list[tuple[float, dict]] = []
        for row in rows:
            domain_match = 1.0 if row["domain"] in domains else 0.0
            kw_overlap   = sum(1 for k in keywords if k in (row["keywords"] or ""))
            score        = domain_match * 2 + kw_overlap
            if score > 0:
                scored.append((score, {
                    "tool_name": row["tool_name"],
                    "domain":    row["domain"],
                    "skill":     row["skill"] or "",
                    "count":     row["count"] or 0,
                }))

        scored.sort(key=lambda x: -x[0])
        hints = [h for _, h in scored[:5]]
        _log.info("[score_tools] returned=%d tools=%s", len(hints), [h["tool_name"] for h in hints])
        return {"tool_hints": hints}
