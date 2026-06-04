"""UserPromptSubmit chain nodes."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from langchain_learning.config import config as _cfg
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


def _tokenise(text: str) -> list[str]:
    import re
    return [t for t in re.findall(r"[a-z]{3,}", text.lower()) if t]


class LoadTurnNode:
    """Read the current turn counter from sessions.db."""

    def __call__(self, state: SessionState) -> dict:
        from langchain_learning import session_graph as sg
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
                _log.debug("load_turn: session=%s turn=%d", session_id, row[0])
                return {"turn": row[0]}
        except Exception as exc:
            _log.warning("load_turn DB error: %s", exc)
        return {}


class LoadMemoriesNode:
    """Score MEMORY.sqlite rows against current prompt keywords."""

    def __call__(self, state: SessionState) -> dict:
        prompt = state["prompt"].lower()
        tokens = set(_tokenise(prompt))

        if not _cfg.memory_db.exists():
            _log.warning("MEMORY.sqlite not found at %s", _cfg.memory_db)
            return {"memories": [], "keywords": list(tokens)}

        scored: list[tuple[float, dict]] = []
        try:
            conn = sqlite3.connect(f"file:{_cfg.memory_db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT name, type, domain, priority, tags, body FROM memories"
            ).fetchall()
            conn.close()
        except Exception as exc:
            _log.error("load_memories DB error: %s", exc)
            return {"memories": [], "keywords": list(tokens)}

        for row in rows:
            if row["priority"] == 1:
                scored.append((1.0, dict(row)))
                continue
            haystack = f"{row['tags'] or ''} {row['body'] or ''}".lower()
            overlap = sum(1 for t in tokens if t in haystack)
            if overlap > 0:
                scored.append((overlap / max(len(tokens), 1), dict(row)))

        scored.sort(key=lambda x: (-x[0], x[1].get("priority", 50)))
        return {
            "memories": [m for _, m in scored[:10]],
            "keywords": list(tokens),
        }


class LoadSessionContextNode:
    """Keyword-search session_summaries and return top-2 as a formatted string."""

    def __call__(self, state: SessionState) -> dict:
        from langchain_learning import session_graph as sg
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
            _log.error("load_session_context DB error: %s", exc)
            return {"session_context": "", "session_context_ids": []}

        def _score(row) -> int:
            tag_hits  = sum(3 for t in (row["tags"] or "").split(",") if t.strip() in keywords)
            body_hits = sum(1 for w in row["summary"].lower().split() if w.strip(".,;:") in keywords)
            return tag_hits + body_hits

        scored = sorted(rows, key=_score, reverse=True)
        top2   = [r for r in scored[:2] if _score(r) > 0]
        if not top2:
            return {"session_context": "", "session_context_ids": []}

        lines, ids = [], []
        for r in top2:
            tag_hint = ", ".join(t.strip() for t in (r["tags"] or "").split(",") if t.strip())[:80]
            preview  = (r["summary"] or "")[:200]
            lines.append(f"- [{r['session_id'][:8]}] ({tag_hint}): {preview}")
            ids.append(r["session_id"])

        _log.info("load_session_context: injecting ids=%s", ids)
        return {"session_context": "\n".join(lines), "session_context_ids": ids}


class ClassifyDomainNode:
    """Detect active domains from keyword overlap and top memory domains."""

    _DOMAIN_VOCAB: dict[str, set[str]] = {
        "astrology":    {"nakshatra", "panchang", "rahu", "ketu", "dasha", "tithi", "lagna", "graha", "jyotish"},
        "market-intel": {"gold", "nifty", "sensex", "fii", "dii", "market", "stock", "equity", "portfolio"},
        "vault":        {"vault", "note", "write", "document", "save", "capture"},
        "macos":        {"message", "calendar", "contact", "reminder", "mail", "imessage", "safari", "music"},
        "health":       {"health", "sleep", "exercise", "weight", "calories", "heart"},
        "philosophy":   {"philosophy", "vedanta", "advaita", "consciousness", "brahman"},
        "coding-best-practices": {"python", "code", "function", "class", "test", "async", "typing"},
    }

    def __call__(self, state: SessionState) -> dict:
        keywords = set(state["keywords"])
        memories = state["memories"]

        detected: set[str] = set()
        for domain, vocab in self._DOMAIN_VOCAB.items():
            if keywords & vocab:
                detected.add(domain)
        for mem in memories[:3]:
            d = mem.get("domain", "global")
            if d and d != "global" and d in _cfg.valid_domains:
                detected.add(d)

        domains = sorted(detected)
        _log.debug("classify_domain: domains=%s skip_tools=%s", domains, not domains)
        return {"domains": domains, "skip_tools": not domains}


class ScoreToolsNode:
    """Retrieve relevant tool hints from tool_hints.sqlite."""

    def __call__(self, state: SessionState) -> dict:
        domains  = set(state["domains"])
        keywords = set(state["keywords"])

        if not _cfg.tool_hints_db.exists():
            _log.warning("tool_hints.sqlite not found at %s", _cfg.tool_hints_db)
            return {"tool_hints": []}

        try:
            conn = sqlite3.connect(f"file:{_cfg.tool_hints_db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT tool_name, domain, skill, count, keywords FROM mcp_tool_hints"
            ).fetchall()
            conn.close()
        except Exception as exc:
            _log.error("score_tools DB error: %s", exc)
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
        _log.debug("score_tools: domains=%s returned=%d tools", list(domains), len(hints))
        return {"tool_hints": hints}


class PersistSessionNode:
    """Write session state snapshot to sessions.db."""

    def __call__(self, state: SessionState) -> dict:
        from langchain_learning import session_graph as sg
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
        except Exception:
            pass

        return {"turn": new_turn}
