"""Server session memory — durable consolidated context store (SQLite).

Purpose: a single all-in-one-place record of recent prompts, MCP tool calls, and
activated tasks across Claude sessions and server runs. It answers "what was I
working on?" — context for the user / cold-start. Deliberately redundant with
task_events / hook logs; the value is consolidation in one queryable place.

SQLite-backed so it survives uvicorn --reload and process restarts. An in-RAM
class/classmethod cannot persist a restart (reload = new process = wiped memory);
the durability lives in the DB. SERVER_SESSION_ID is just a column tag per row,
not a lifecycle boundary.

Population happens at the HTTP hook boundary (server.py), not in the graph.
Read via get_server_memory() / GET /session/memory / hooks__server_memory.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path

from src.logger import get_logger

_log = get_logger(__name__)

# Identity of this server run — a tag column on each row, distinct from the Claude
# session id. Regenerated per process; the DB (and thus history) is unaffected.
SERVER_SESSION_ID = uuid.uuid4().hex[:12]
STARTED_AT = time.time()

# Skip test sessions so the durable store isn't polluted (past_mistakes.md #5).
_TEST_PREFIXES = ("test-", "pytest-", "api-test-")


class ServerMemory:
    """SQLite-backed consolidated context store. Classmethods are the API."""

    _DB = Path.home() / ".claude" / "server_memory.sqlite"

    # ── storage ──────────────────────────────────────────────────────────────

    @classmethod
    def _connect(cls) -> sqlite3.Connection:
        conn = sqlite3.connect(str(cls._DB), timeout=5)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS server_memory (
                   id                INTEGER PRIMARY KEY,
                   server_session_id TEXT,
                   claude_session_id TEXT,
                   ts                REAL,
                   prompt            TEXT,
                   tool              TEXT,
                   task_id           TEXT,
                   task_title        TEXT
               )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sm_ts ON server_memory(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sm_session ON server_memory(claude_session_id)")
        return conn

    @classmethod
    def _insert(cls, claude_session_id: str, *, prompt=None, tool=None,
                task_id=None, task_title=None) -> None:
        if (claude_session_id or "").startswith(_TEST_PREFIXES):
            return
        try:
            conn = cls._connect()
            try:
                conn.execute(
                    """INSERT INTO server_memory
                       (server_session_id, claude_session_id, ts, prompt, tool, task_id, task_title)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (SERVER_SESSION_ID, claude_session_id or "", time.time(),
                     prompt, tool, task_id, task_title),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            _log.warning("[server_memory] insert failed: %s", exc)

    # ── record ───────────────────────────────────────────────────────────────

    @classmethod
    def record_prompt(cls, claude_session_id: str, text: str) -> None:
        if text:
            cls._insert(claude_session_id, prompt=text)

    @classmethod
    def record_tool(cls, claude_session_id: str, tool: str) -> None:
        if tool:
            cls._insert(claude_session_id, tool=tool)

    @classmethod
    def record_task(cls, claude_session_id: str, task_id: str, title: str) -> None:
        if task_id:
            cls._insert(claude_session_id, task_id=task_id, task_title=title or "")

    # ── read ─────────────────────────────────────────────────────────────────

    @classmethod
    def get(cls, n_prompts: int = 20, m_tasks: int = 10, k_tools: int = 30) -> dict:
        """Return last N prompts, M tasks, K tool calls (chronological) + totals."""
        empty = {
            "server_session_id": SERVER_SESSION_ID, "started_at": STARTED_AT,
            "n_prompts_total": 0, "n_tasks_total": 0, "n_tools_total": 0,
            "prompts": [], "tasks": [], "tools": [],
        }
        try:
            conn = cls._connect()
            conn.row_factory = sqlite3.Row
            try:
                def _recent(where: str, cols: str, lim: int) -> list[dict]:
                    if lim <= 0:
                        return []
                    rows = conn.execute(
                        f"SELECT claude_session_id, ts, {cols} FROM server_memory "
                        f"WHERE {where} IS NOT NULL ORDER BY id DESC LIMIT ?",
                        (lim,),
                    ).fetchall()
                    return [dict(r) for r in reversed(rows)]

                prompts = _recent("prompt", "prompt AS text", max(0, n_prompts))
                tasks = _recent("task_id", "task_id, task_title AS title", max(0, m_tasks))
                tools = _recent("tool", "tool", max(0, k_tools))
                totals = conn.execute(
                    """SELECT SUM(prompt IS NOT NULL), SUM(task_id IS NOT NULL),
                              SUM(tool IS NOT NULL) FROM server_memory"""
                ).fetchone()
            finally:
                conn.close()
        except Exception as exc:
            _log.warning("[server_memory] get failed: %s", exc)
            return empty
        return {
            "server_session_id": SERVER_SESSION_ID,
            "started_at": STARTED_AT,
            "n_prompts_total": totals[0] or 0,
            "n_tasks_total": totals[1] or 0,
            "n_tools_total": totals[2] or 0,
            "prompts": prompts,
            "tasks": tasks,
            "tools": tools,
        }

    @classmethod
    def reset(cls) -> None:
        """Delete all rows — test helper / manual clear."""
        try:
            conn = cls._connect()
            try:
                conn.execute("DELETE FROM server_memory")
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            _log.warning("[server_memory] reset failed: %s", exc)


# ---------------------------------------------------------------------------
# Module-level API — thin delegators + hook-payload helpers (keep call sites stable)
# ---------------------------------------------------------------------------

def record_prompt(claude_session_id: str, text: str) -> None:
    ServerMemory.record_prompt(claude_session_id, text)


def record_tool(claude_session_id: str, tool: str) -> None:
    ServerMemory.record_tool(claude_session_id, tool)


def record_task(claude_session_id: str, task_id: str, title: str) -> None:
    ServerMemory.record_task(claude_session_id, task_id, title)


def get_server_memory(n_prompts: int = 20, m_tasks: int = 10, k_tools: int = 30) -> dict:
    return ServerMemory.get(n_prompts=n_prompts, m_tasks=m_tasks, k_tools=k_tools)


def reset() -> None:
    ServerMemory.reset()


def _title_from_response(tresp) -> str:
    """Pull title from a PostToolUse tool_response, unwrapping the MCP content envelope.

    Claude Code wraps MCP results as {"content": [{"type": "text", "text": "<json>"}]}.
    Best-effort — the envelope shape varies, so DB lookup is the authoritative source.
    """
    if not isinstance(tresp, dict):
        return ""
    if isinstance(tresp.get("content"), list) and tresp["content"]:
        import json
        try:
            tresp = json.loads(tresp["content"][0].get("text", "") or "{}")
        except Exception:
            return ""
    return tresp.get("title", "") if isinstance(tresp, dict) else ""


def _title_for_task(task_id: str) -> str:
    """Authoritative title lookup from proj_tasks.db (read-only), like activate_task does."""
    if not task_id:
        return ""
    try:
        from langchain_learning.config import config as _cfg
        if not _cfg.tasks_db.exists():
            return ""
        conn = sqlite3.connect(f"file:{_cfg.tasks_db}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT title FROM open_tasks WHERE id = ?", (task_id,)).fetchone()
        finally:
            conn.close()
        return row[0] if row else ""
    except Exception as exc:
        _log.warning("server_memory: title lookup failed for %s: %s", task_id, exc)
        return ""


def record_tool_from_hook(body: dict) -> None:
    """Record an MCP tool call (short name only) from a raw PostToolUse hook payload."""
    tool_name = body.get("tool_name", "")
    if not tool_name.startswith("mcp__"):
        return
    try:
        from core.tool_registry import strip_mcp_prefix
        short = strip_mcp_prefix(tool_name) or tool_name
    except Exception:
        short = tool_name
    record_tool(body.get("session_id", ""), short)


def record_task_from_hook(body: dict) -> None:
    """Record a task activation from a raw PostToolUse hook payload.

    Handles the fully-qualified MCP tool_name (mcp__claude-hooks__tasks__set_active);
    title resolved authoritatively from proj_tasks.db, falling back to the response.
    """
    tool_name = body.get("tool_name", "")
    if not tool_name.startswith("mcp__"):
        return
    try:
        from core.tool_registry import strip_mcp_prefix
        short = strip_mcp_prefix(tool_name) or tool_name
    except Exception:
        short = tool_name
    if short != "tasks__set_active":
        return
    tin = body.get("tool_input") or {}
    task_id = tin.get("task_id", "") if isinstance(tin, dict) else ""
    title = _title_for_task(task_id) or _title_from_response(body.get("tool_response"))
    record_task(body.get("session_id", ""), task_id, title)
