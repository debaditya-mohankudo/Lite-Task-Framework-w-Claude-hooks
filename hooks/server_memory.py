"""Server-owned session memory — cross-session recency for cold-start context.

The FastAPI hook server outlives individual Claude sessions, so it keeps a small
in-memory record of recent prompts and activated tasks spanning every Claude
session in this server run. A fresh session (e.g. new day) reads it via
get_server_memory() to bootstrap context instead of cold-starting blind.

Scope (see task:bbdf7850): in-memory only, no persistence. Server restart simply
empties it — by design; deeper history lives in the durable task store. Identity
is the server run (SERVER_SESSION_ID), distinct from the per-conversation Claude
session id used as the MemorySaver thread_id.
"""
from __future__ import annotations

import time
import uuid

from src.logger import get_logger

_log = get_logger(__name__)

_MAX_PROMPTS = 200
_MAX_TASKS = 200
_MAX_TOOLS = 300

# Identity of this server run — distinct from any Claude session id. Regenerated
# on restart, which is exactly when the in-memory store resets.
SERVER_SESSION_ID = uuid.uuid4().hex[:12]
STARTED_AT = time.time()

# Cross-session, append-only, capped. One entry per prompt / task activation / tool call.
_PROMPTS: list[dict] = []   # [{claude_session_id, text, ts}]
_TASKS: list[dict] = []     # [{claude_session_id, task_id, title, ts}]
_TOOLS: list[dict] = []     # [{claude_session_id, tool, ts}] — MCP tool short-names, no args


def record_prompt(claude_session_id: str, prompt: str) -> None:
    """Append a user prompt to the server memory. No-op on empty prompt."""
    if not prompt:
        return
    _PROMPTS.append({"claude_session_id": claude_session_id or "", "text": prompt, "ts": time.time()})
    if len(_PROMPTS) > _MAX_PROMPTS:
        del _PROMPTS[:-_MAX_PROMPTS]


def record_task(claude_session_id: str, task_id: str, title: str) -> None:
    """Append an activated task (id+title). No-op on empty id; dedups consecutive repeats."""
    if not task_id:
        return
    if _TASKS and _TASKS[-1].get("task_id") == task_id:
        return
    _TASKS.append({"claude_session_id": claude_session_id or "", "task_id": task_id, "title": title or "", "ts": time.time()})
    if len(_TASKS) > _MAX_TASKS:
        del _TASKS[:-_MAX_TASKS]


def record_tool(claude_session_id: str, tool: str) -> None:
    """Append an MCP tool short-name. No-op on empty; dedups consecutive repeats."""
    if not tool:
        return
    if _TOOLS and _TOOLS[-1].get("tool") == tool:
        return
    _TOOLS.append({"claude_session_id": claude_session_id or "", "tool": tool, "ts": time.time()})
    if len(_TOOLS) > _MAX_TOOLS:
        del _TOOLS[:-_MAX_TOOLS]


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
        import sqlite3

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


def record_task_from_hook(body: dict) -> None:
    """Record a task activation from a raw PostToolUse hook payload.

    Handles the fully-qualified MCP tool_name (mcp__claude-hooks__tasks__set_active)
    and the wrapped tool_response envelope — mirrors the dispatcher's normalisation
    so the tap actually fires (see bug:7b1084e3).
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
    # DB is authoritative; fall back to the (brittle) response envelope only if needed.
    title = _title_for_task(task_id) or _title_from_response(body.get("tool_response"))
    record_task(body.get("session_id", ""), task_id, title)


def get_server_memory(n_prompts: int = 20, m_tasks: int = 10, k_tools: int = 30) -> dict:
    """Return the last N prompts, M tasks, and K tool calls across this server run.

    The consumer bounds the read; the store stays append-only. Always returns a
    valid dict (empty lists on a fresh server).
    """
    n = max(0, n_prompts)
    m = max(0, m_tasks)
    k = max(0, k_tools)
    return {
        "server_session_id": SERVER_SESSION_ID,
        "started_at": STARTED_AT,
        "n_prompts_total": len(_PROMPTS),
        "n_tasks_total": len(_TASKS),
        "n_tools_total": len(_TOOLS),
        "prompts": _PROMPTS[-n:] if n else [],
        "tasks": _TASKS[-m:] if m else [],
        "tools": _TOOLS[-k:] if k else [],
    }


def reset() -> None:
    """Clear the store — test helper only."""
    _PROMPTS.clear()
    _TASKS.clear()
    _TOOLS.clear()
