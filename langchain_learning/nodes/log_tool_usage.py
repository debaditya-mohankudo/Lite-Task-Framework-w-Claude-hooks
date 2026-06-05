"""LogToolUsageNode — upserts tool hint and appends tool name to prompt_tools state."""
from __future__ import annotations

import json
import sqlite3

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_MAX_RECENT_PROMPTS = 10

# Tools where a non-empty result means "found" — used by gate prereq checks
_SEARCH_TOOLS = {"contacts__search", "mail__search", "reminders__list", "notes__list"}


def _result_found(tool_name: str, tool_result: dict) -> bool:
    """Return True if the tool result indicates a successful non-empty response."""
    if not tool_result:
        return False
    if tool_name in _SEARCH_TOOLS:
        # contacts__search returns {name, phoneNumbers, ...} — found if phoneNumbers non-empty
        if tool_name == "contacts__search":
            return bool(tool_result.get("phoneNumbers") or tool_result.get("name"))
        return bool(tool_result)
    # For other tools, any non-error result counts as found
    return "error" not in tool_result

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


def _append_prompt(existing_json: str, new_prompt: str) -> str:
    if not new_prompt:
        return existing_json
    try:
        prompts: list[str] = json.loads(existing_json or "[]")
    except Exception:
        prompts = []
    prompts.append(new_prompt)
    return json.dumps(prompts[-_MAX_RECENT_PROMPTS:])


class LogToolUsageNode:
    """Upsert tool hint row in tool_hints.sqlite and record prompt_tool_call in sessions.db."""

    def __call__(self, state: SessionState) -> dict:
        from core.tool_registry import infer_domain, infer_skill, strip_mcp_prefix

        tool_name   = state.get("tool_name", "")
        tool_name   = strip_mcp_prefix(tool_name) or tool_name
        duration_ms = float(state.get("duration_ms", 0.0))
        prompt_id   = state.get("prompt_id", "")

        entry("log_tool_usage", state, duration_ms=round(duration_ms, 1))

        if not tool_name:
            return {}

        domain = infer_domain(tool_name)
        skill  = infer_skill(tool_name)
        prompt = state.get("prompt", "")
        tool_result = state.get("tool_result") or {}
        if tool_name == "contacts__search":
            _log.debug("[log_tool_usage] contacts__search raw result: %s", json.dumps(tool_result, default=str))
        found = _result_found(tool_name, tool_result)

        if tool_name == "confirm__send":
            if tool_result.get("confirmed"):
                _log.info(
                    "[confirm__send] token written session=%s prompt_id=%s recipient=%s",
                    state.get("session_id", "?")[:8],
                    prompt_id[:8] if prompt_id else "?",
                    tool_result.get("recipient", "?"),
                )
            else:
                _log.error(
                    "[confirm__send] failed session=%s prompt_id=%s error=%s",
                    state.get("session_id", "?")[:8],
                    prompt_id[:8] if prompt_id else "?",
                    tool_result.get("error", "unknown"),
                )

        self._upsert_tool_hint(tool_name, domain, skill, duration_ms, prompt)
        _log.info("[log_tool_usage] tool=%s domain=%s latency=%.1fms found=%s prompt=%s",
                  tool_name, domain, duration_ms, found, prompt_id[:8] if prompt_id else "?")

        existing = list(state.get("prompt_tools") or [])
        existing.append({"tool": tool_name, "found": found, "tool_result": tool_result})

        from collections import OrderedDict
        tool_input  = state.get("tool_input") or {}
        session_tools: OrderedDict[str, list[dict]] = OrderedDict(state.get("session_tools") or {})
        if prompt_id:
            bucket = list(session_tools.get(prompt_id) or [])
            bucket.append({"tool": tool_name, "tool_input": tool_input})
            session_tools[prompt_id] = bucket
            _log.debug("[log_tool_usage] session_tools[%s]=%s", prompt_id[:8], [e["tool"] for e in bucket])

        return {"prompt_tools": existing, "session_tools": session_tools}

    def _upsert_tool_hint(self, short_name: str, domain: str, skill: str, latency_ms: float, prompt_text: str = "") -> None:
        tool_hints_db = _cfg.tool_hints_db
        if not tool_hints_db.exists():
            return
        try:
            with sqlite3.connect(str(tool_hints_db)) as conn:
                conn.execute(_ENSURE_HINTS)
                cols = {r[1] for r in conn.execute("PRAGMA table_info(mcp_tool_hints)").fetchall()}
                for col, defval in [("keywords", "''"), ("skill", "''"),
                                     ("recent_prompts", "'[]'"), ("embedding", "NULL")]:
                    if col not in cols:
                        conn.execute(f"ALTER TABLE mcp_tool_hints ADD COLUMN {col} TEXT DEFAULT {defval}")

                row = conn.execute(
                    "SELECT count, avg_latency_ms, recent_prompts FROM mcp_tool_hints WHERE tool_name = ?",
                    (short_name,)
                ).fetchone()

                if row:
                    new_count   = row[0] + 1
                    new_avg     = (row[1] * row[0] + latency_ms) / new_count
                    new_prompts = _append_prompt(row[2] or "[]", prompt_text)
                    conn.execute(
                        "UPDATE mcp_tool_hints SET count=?, last_used=datetime('now'), avg_latency_ms=?, domain=?, skill=?, recent_prompts=? WHERE tool_name=?",
                        (new_count, round(new_avg, 2), domain, skill, new_prompts, short_name),
                    )
                else:
                    new_prompts = _append_prompt("[]", prompt_text)
                    conn.execute(
                        "INSERT INTO mcp_tool_hints (tool_name, domain, count, last_used, avg_latency_ms, skill, recent_prompts) VALUES (?, ?, 1, datetime('now'), ?, ?, ?)",
                        (short_name, domain, round(latency_ms, 2), skill, new_prompts),
                    )
                conn.commit()
        except Exception as exc:
            _log.warning("[log_tool_usage] upsert failed for %r: %s", short_name, exc)
