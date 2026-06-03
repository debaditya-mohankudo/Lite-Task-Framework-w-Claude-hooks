#!/usr/bin/env python3
"""
PostToolUse hook — LangChain variant (no HTTP).

Replaces: tool_usage_logger.py → POST /hook/posttool → server/core/handlers/posttool_handler.py

Inlines PostToolHandler logic directly: upserts tool_hints.sqlite, records
prompt_tool_calls in sessions.db. No FastAPI dependency.
"""
import json
import os
import sqlite3
from pathlib import Path
import sys

_PROJECT_ROOT = Path.home() / "workspace/claude-hooks"
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.config import config as _cfg
_TOOL_HINTS_DB = _cfg.tool_hints_db
_SESSIONS_DB   = _cfg.sessions_db
from sqlite_log_handler import setup
from utils import read_stdin, write_json_to_stdout

from core.tool_registry import strip_mcp_prefix, infer_domain, infer_skill
from core.db.session_db import SessionDB

log = setup("tool_usage_logger_lc")
_PROMPT_KW_TMP   = Path.home() / ".claude/current_prompt_keywords.tmp"
_PROMPT_TEXT_TMP = Path.home() / ".claude/current_prompt_text.tmp"
_MAX_RECENT_PROMPTS = 10

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


def _upsert_tool_hint(short_name: str, domain: str, skill: str, latency_ms: float) -> None:
    if not _TOOL_HINTS_DB.exists():
        return
    prompt_keywords = _read_tmp(_PROMPT_KW_TMP)
    prompt_text     = _read_tmp(_PROMPT_TEXT_TMP)
    try:
        with sqlite3.connect(str(_TOOL_HINTS_DB)) as conn:
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
                    """UPDATE mcp_tool_hints
                       SET count=?, last_used=datetime('now'), avg_latency_ms=?,
                           domain=?, keywords=?, skill=?, recent_prompts=?
                       WHERE tool_name=?""",
                    (new_count, round(new_avg, 2), domain, new_kw, skill, new_prompts, short_name)
                )
            else:
                new_prompts = _append_prompt("[]", prompt_text)
                conn.execute(
                    """INSERT INTO mcp_tool_hints
                       (tool_name, domain, count, last_used, avg_latency_ms, keywords, skill, recent_prompts)
                       VALUES (?, ?, 1, datetime('now'), ?, ?, ?, ?)""",
                    (short_name, domain, round(latency_ms, 2), prompt_keywords, skill, new_prompts)
                )
            conn.commit()
    except Exception as exc:
        log.warning("tool_hints upsert failed for %r: %s", short_name, exc)


def main():
    try:
        hook_input  = read_stdin()
        tool_name   = hook_input.get("tool_name", "")
        session_id  = hook_input.get("session_id", "")
        duration_ms = float(hook_input.get("duration_ms", 0))
        tool_input  = hook_input.get("tool_input", {})
        tool_use_id = os.environ.get("ANTHROPIC_TOOL_USE_ID", "")
        prompt_id   = tool_use_id or hook_input.get("prompt_id", "")

        if not tool_name or not tool_name.startswith("mcp__"):
            write_json_to_stdout()
            return

        short_name = strip_mcp_prefix(tool_name) or tool_name
        if short_name.startswith("memory__"):
            write_json_to_stdout()
            return

        domain = infer_domain(short_name)
        skill  = infer_skill(short_name)
        args   = tool_input if isinstance(tool_input, dict) else {}

        _upsert_tool_hint(short_name, domain, skill, duration_ms)
        log.debug("tool hint upserted: %s domain=%s latency=%.1fms", short_name, domain, duration_ms)

        if session_id:
            db = SessionDB.open(_SESSIONS_DB)
            db.record_prompt_tool(prompt_id, session_id, short_name, args, tool_use_id)
            log.debug("tool call recorded: %s session=%s prompt=%s", short_name, session_id, prompt_id)

    except Exception as e:
        log.error("tool_usage_logger_lc failed: %s", e)
        write_json_to_stdout(error=f"tool_usage_logger_lc failed: {e}")
    else:
        write_json_to_stdout()


if __name__ == "__main__":
    main()
