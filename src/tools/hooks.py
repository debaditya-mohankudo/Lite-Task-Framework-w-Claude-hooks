"""MCP tools for querying hook state and logs.

NOTE: checkpoint_query reads langgraph_checkpoints.db which is no longer written
to in production (replaced by MemorySaver in the FastAPI server as of 2026-06-14).
Use `curl http://127.0.0.1:8766/session` for live session info instead.
checkpoint_query is retained for historical/test use only.
"""
import json
import sqlite3
import urllib.request
from pathlib import Path
from typing import Optional

import msgpack

_DB_PATH = Path.home() / ".claude" / "langgraph_checkpoints.db"
_HOOKS_LOG_DB = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Databases" / "claude_hooks.sqlite"
_SERVER_URL = "http://127.0.0.1:8766"


def handle_server_memory(n_events: int = 50) -> dict:
    """Last N events from the server's unified event log — "what was I working on?".

    Free-flowing chronological sequence of prompts, MCP tool calls, task activations,
    and assistant turns with timestamps. SQLite-backed, survives server reloads,
    capped to a rolling window. Returns {error: ...} if the server is unreachable.

    Args:
        n_events: Max recent events to return (default 50).
    """
    url = f"{_SERVER_URL}/session/memory?n_events={int(n_events)}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        return {"error": f"hook server unreachable ({exc}); server memory is in-process and only available while it runs"}


def _decode(value: bytes | None) -> object:
    if value is None:
        return None
    try:
        return msgpack.unpackb(value, raw=False)
    except Exception:
        return value.decode("utf-8", errors="replace")


def handle_checkpoint_query(thread_id: str = "") -> dict:
    """Query the latest LangGraph checkpoint for injected memories, tool hints, session context, domains, and keywords.

    If thread_id is omitted, returns the most recent checkpoint across all threads.

    DEPRECATED in production: langgraph_checkpoints.db is no longer written to since the
    FastAPI server (hooks/server.py) uses MemorySaver. Use GET /session for live session info.
    """
    if not _DB_PATH.exists():
        return {"error": f"DB not found: {_DB_PATH}"}

    with sqlite3.connect(_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        # Find the latest checkpoint that has a 'memories' write (skip stop/output events)
        if thread_id:
            row = conn.execute(
                """SELECT c.thread_id, c.checkpoint_id FROM checkpoints c
                   JOIN writes w ON c.thread_id = w.thread_id AND c.checkpoint_id = w.checkpoint_id
                   WHERE c.thread_id = ? AND w.channel = 'memories'
                   ORDER BY c.checkpoint_id DESC LIMIT 1""",
                (thread_id,),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT c.thread_id, c.checkpoint_id FROM checkpoints c
                   JOIN writes w ON c.thread_id = w.thread_id AND c.checkpoint_id = w.checkpoint_id
                   WHERE w.channel = 'memories'
                   ORDER BY c.checkpoint_id DESC LIMIT 1"""
            ).fetchone()

        if not row:
            return {"error": "No checkpoints found"}

        tid = row["thread_id"]
        cid = row["checkpoint_id"]

        writes = conn.execute(
            "SELECT channel, value FROM writes WHERE thread_id = ? AND checkpoint_id = ?",
            (tid, cid),
        ).fetchall()

    channels = {w["channel"]: _decode(w["value"]) for w in writes}

    memories_raw = channels.get("memories", [])
    memories = []
    if isinstance(memories_raw, list):
        for m in memories_raw:
            if isinstance(m, dict):
                memories.append({
                    "name": m.get("name"),
                    "type": m.get("type"),
                    "domain": m.get("domain"),
                    "priority": m.get("priority"),
                    "tags": m.get("tags"),
                    "body": (m.get("body") or "")[:200],
                })

    tool_hints_raw = channels.get("tool_hints", [])
    tool_hints = []
    if isinstance(tool_hints_raw, list):
        for h in tool_hints_raw:
            if isinstance(h, dict):
                tool_hints.append({
                    "tool": h.get("tool_name"),
                    "domain": h.get("domain"),
                    "skill": h.get("skill"),
                    "count": h.get("count"),
                })

    session_context_raw = channels.get("session_context", [])
    session_context = []
    if isinstance(session_context_raw, list):
        for s in session_context_raw:
            session_context.append(str(s)[:300] if s else "")

    return {
        "thread_id": tid,
        "checkpoint_id": cid,
        "prompt_id": channels.get("prompt_id"),
        "domains": channels.get("domains", []),
        "keywords": channels.get("keywords", []),
        "matched_keywords": channels.get("matched_keywords"),
        "memories": memories,
        "tool_hints": tool_hints,
        "session_context": session_context,
        "event_type": channels.get("event_type"),
        "cwd": channels.get("cwd"),
        "turn": channels.get("turn"),
    }


def handle_read_logs_sqlite(
    level: str = "",
    logger: str = "",
    search: str = "",
    limit: int = 50,
) -> dict:
    """Query hook_logs from claude_hooks.sqlite.

    Args:
        level:  Filter by log level (e.g. INFO, WARNING, ERROR). Empty = all.
        logger: Filter by logger name substring. Empty = all.
        search: Substring to match in the message field. Empty = all.
        limit:  Max rows to return (default 50, max 200).
    """
    if not _HOOKS_LOG_DB.exists():
        return {"error": f"DB not found: {_HOOKS_LOG_DB}"}

    limit = min(limit, 200)
    conditions = []
    params: list = []

    if level:
        conditions.append("level = ?")
        params.append(level.upper())
    if logger:
        conditions.append("logger LIKE ?")
        params.append(f"%{logger}%")
    if search:
        conditions.append("message LIKE ?")
        params.append(f"%{search}%")

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(limit)

    with sqlite3.connect(_HOOKS_LOG_DB) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"SELECT id, ts, level, logger, message FROM hook_logs {where} ORDER BY id DESC LIMIT ?",
            params,
        ).fetchall()

    return {
        "count": len(rows),
        "rows": [dict(r) for r in rows],
    }
