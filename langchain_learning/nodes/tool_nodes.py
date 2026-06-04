"""PreToolUse and PostToolUse chain nodes."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from langchain_learning.config import config as _cfg
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_MAX_RECENT_PROMPTS = 10
_PROMPT_KW_TMP   = Path.home() / ".claude/current_prompt_keywords.tmp"
_PROMPT_TEXT_TMP = Path.home() / ".claude/current_prompt_text.tmp"

_ENSURE_HINTS = """
CREATE TABLE IF NOT EXISTS mcp_tool_hints (
    tool_name       TEXT PRIMARY KEY,
    domain          TEXT,
    count           INTEGER DEFAULT 0,
    last_used       TIMESTAMP,
    avg_latency_ms  REAL DEFAULT 0.0,
    keywords        TEXT DEFAULT '',
    skill           TEXT DEFAULT '',
    recent_prompts  TEXT DEFAULT '[]',
    embedding       BLOB
)
"""


def _read_tmp(path: Path) -> str:
    try:
        return path.read_text().strip()
    except Exception:
        return ""


def _merge_keywords(existing: str, new_kw: str) -> str:
    combined = set(filter(None, existing.split(","))) | set(filter(None, new_kw.split(",")))
    return ",".join(sorted(combined))


def _append_prompt(existing_json: str, new_prompt: str) -> str:
    if not new_prompt:
        return existing_json
    try:
        prompts: list[str] = json.loads(existing_json or "[]")
    except Exception:
        prompts = []
    prompts.append(new_prompt)
    return json.dumps(prompts[-_MAX_RECENT_PROMPTS:])


class GateCheckNode:
    """Run gate policy — sets gate_denied + gate_reason in state."""

    def __call__(self, state: SessionState) -> dict:
        from langchain_learning import session_graph as sg
        from hooks.gates import check as _gate_check
        from core.db.session_db import SessionDB

        tool_name  = state.get("tool_name", "")
        tool_input = state.get("tool_input") or {}
        prompt_id  = state.get("prompt_id", "")

        if not tool_name:
            return {"gate_denied": False, "gate_reason": ""}

        sessions_db = sg._SESSIONS_DB or Path.home() / ".claude" / "sessions.db"
        db = SessionDB.open(sessions_db)
        deny, reason = _gate_check(
            tool_name,
            lambda prereq: db.prompt_had_tool(prompt_id, prereq),
            tool_input,
        )
        if deny:
            _log.warning("gate_check DENY %s (prompt_id=%s): %s", tool_name, prompt_id, reason)
        else:
            _log.info("gate_check ALLOW %s (prompt_id=%s)", tool_name, prompt_id)
        return {"gate_denied": deny, "gate_reason": reason}


class LogToolUsageNode:
    """Upsert tool hint row and record prompt_tool_call in sessions.db."""

    def __call__(self, state: SessionState) -> dict:
        from langchain_learning import session_graph as sg
        from core.tool_registry import infer_domain, infer_skill
        from core.db.session_db import SessionDB

        tool_name   = state.get("tool_name", "")
        session_id  = state.get("session_id", "")
        duration_ms = float(state.get("duration_ms", 0.0))
        tool_input  = state.get("tool_input") or {}
        tool_use_id = state.get("tool_use_id", "")
        prompt_id   = state.get("prompt_id", "")

        if not tool_name:
            return {}

        domain = infer_domain(tool_name)
        skill  = infer_skill(tool_name)
        self._upsert_tool_hint(tool_name, domain, skill, duration_ms)
        _log.debug("log_tool_usage: %s domain=%s latency=%.1fms", tool_name, domain, duration_ms)

        if session_id:
            sessions_db = sg._SESSIONS_DB or Path.home() / ".claude" / "sessions.db"
            db = SessionDB.open(sessions_db)
            db.record_prompt_tool(prompt_id, session_id, tool_name, tool_input, tool_use_id)
            _log.debug("log_tool_usage: recorded %s session=%s prompt=%s", tool_name, session_id, prompt_id)

        return {}

    def _upsert_tool_hint(self, short_name: str, domain: str, skill: str, latency_ms: float) -> None:
        tool_hints_db = _cfg.tool_hints_db
        if not tool_hints_db.exists():
            return
        prompt_keywords = _read_tmp(_PROMPT_KW_TMP)
        prompt_text     = _read_tmp(_PROMPT_TEXT_TMP)
        try:
            with sqlite3.connect(str(tool_hints_db)) as conn:
                conn.execute(_ENSURE_HINTS)
                cols = {r[1] for r in conn.execute("PRAGMA table_info(mcp_tool_hints)").fetchall()}
                for col, defval in [("keywords", "''"), ("skill", "''"),
                                     ("recent_prompts", "'[]'"), ("embedding", "NULL")]:
                    if col not in cols:
                        conn.execute(f"ALTER TABLE mcp_tool_hints ADD COLUMN {col} TEXT DEFAULT {defval}")

                row = conn.execute(
                    "SELECT count, avg_latency_ms, keywords, recent_prompts FROM mcp_tool_hints WHERE tool_name = ?",
                    (short_name,)
                ).fetchone()

                if row:
                    new_count   = row[0] + 1
                    new_avg     = (row[1] * row[0] + latency_ms) / new_count
                    new_kw      = _merge_keywords(row[2] or "", prompt_keywords)
                    new_prompts = _append_prompt(row[3] or "[]", prompt_text)
                    conn.execute(
                        "UPDATE mcp_tool_hints SET count=?, last_used=datetime('now'), avg_latency_ms=?, domain=?, keywords=?, skill=?, recent_prompts=? WHERE tool_name=?",
                        (new_count, round(new_avg, 2), domain, new_kw, skill, new_prompts, short_name),
                    )
                else:
                    new_prompts = _append_prompt("[]", prompt_text)
                    conn.execute(
                        "INSERT INTO mcp_tool_hints (tool_name, domain, count, last_used, avg_latency_ms, keywords, skill, recent_prompts) VALUES (?, ?, 1, datetime('now'), ?, ?, ?, ?)",
                        (short_name, domain, round(latency_ms, 2), prompt_keywords, skill, new_prompts),
                    )
                conn.commit()
        except Exception as exc:
            _log.warning("_upsert_tool_hint failed for %r: %s", short_name, exc)
