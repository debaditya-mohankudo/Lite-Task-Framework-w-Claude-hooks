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

# Identity of this server run — distinct from any Claude session id. Regenerated
# on restart, which is exactly when the in-memory store resets.
SERVER_SESSION_ID = uuid.uuid4().hex[:12]
STARTED_AT = time.time()

# Cross-session, append-only, capped. One entry per prompt / per task activation.
_PROMPTS: list[dict] = []   # [{claude_session_id, text, ts}]
_TASKS: list[dict] = []     # [{claude_session_id, task_id, title, ts}]


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


def get_server_memory(n_prompts: int = 20, m_tasks: int = 10) -> dict:
    """Return the last N prompts and last M tasks across this server run.

    The consumer bounds the read; the store stays append-only. Always returns a
    valid dict (empty lists on a fresh server).
    """
    n = max(0, n_prompts)
    m = max(0, m_tasks)
    return {
        "server_session_id": SERVER_SESSION_ID,
        "started_at": STARTED_AT,
        "n_prompts_total": len(_PROMPTS),
        "n_tasks_total": len(_TASKS),
        "prompts": _PROMPTS[-n:] if n else [],
        "tasks": _TASKS[-m:] if m else [],
    }


def reset() -> None:
    """Clear the store — test helper only."""
    _PROMPTS.clear()
    _TASKS.clear()
