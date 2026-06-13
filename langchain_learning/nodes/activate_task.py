"""ActivateTaskNode — PostToolUse node for task activation and stack pop.

Handles:
  tasks__set_active  — reads task_id from tool_input, activates task in checkpoint
  tasks__pop_active  — pops the task_stack and re-activates the previous task

DB logic is inlined (not delegated to SetActiveTaskNode/LoadTaskMemoriesNode) so
those nodes' entry() calls don't pollute the PostToolUse log stream with wrong event context.

Tags: task-activation, post-tool-use, checkpoint, active-task, task-stack
"""
from __future__ import annotations

import sqlite3

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.nodes._text_utils import tokenise, task_project_tag
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_ACTIVATING_TOOLS = {"tasks__set_active", "tasks__pop_active"}


def _lookup_task(task_id: str) -> tuple[str, str] | None:
    """Return (title, body) for task_id, marking open→wip. None if not found."""
    if not _cfg.tasks_db.exists():
        return None
    try:
        with sqlite3.connect(str(_cfg.tasks_db), timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT title, body, status FROM open_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                return None
            if row["status"] == "open":
                conn.execute(
                    "UPDATE open_tasks SET status='wip', updated_at=datetime('now') WHERE id=?",
                    (task_id,),
                )
            return row["title"], row["body"] or ""
    except Exception as exc:
        _log.error("[activate_task] DB error looking up task=%s: %s", task_id, exc)
        return None


def _score_memories(task_id: str, task_title: str) -> list[dict]:
    """Score MEMORY.sqlite rows against task title tokens. Returns top-5."""
    tokens = tokenise(task_title)
    if not tokens or not _cfg.memory_db.exists():
        return []
    project = task_project_tag(task_id, _cfg.tasks_db)
    try:
        conn = sqlite3.connect(f"file:{_cfg.memory_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name, type, domain, priority, tags, body FROM memories"
        ).fetchall()
        conn.close()
    except Exception as exc:
        _log.warning("[activate_task] memory DB error: %s", exc)
        return []
    scored: list[tuple[float, dict]] = []
    for row in rows:
        if project and row["domain"] not in (project, "global"):
            continue
        haystack = f"{row['tags'] or ''} {row['body'] or ''}".lower()
        overlap = sum(1 for t in tokens if t in haystack)
        if overlap > 0:
            scored.append((overlap / max(len(tokens), 1), dict(row)))
    scored.sort(key=lambda x: (-x[0], x[1].get("priority", 50)))
    return [m for _, m in scored[:5]]


def _activate(state: SessionState, task_id: str, task_stack: list) -> dict:
    """Resolve task from DB + score memories. Returns state update dict."""
    result = _lookup_task(task_id)
    if result is None:
        _log.warning("[activate_task] task_id=%s not found in proj_tasks.db", task_id)
        return {}
    title, body = result
    memories = _score_memories(task_id, title)
    return {
        "active_task_id":    task_id,
        "active_task_title": title,
        "task_body":         body,
        "task_memories":     memories,
        "task_stack":        task_stack,
    }


class ActivateTaskNode:
    """PostToolUse bridge for tasks__set_active and tasks__pop_active.

    tasks__set_active: reads task_id from tool_input, activates task in checkpoint.
    tasks__pop_active: pops task_stack and re-activates the previous task.
    No-ops for any other tool name.

    Tags: task-activation, post-tool-use, checkpoint, active-task, task-stack
    """

    def __call__(self, state: SessionState) -> dict:
        entry("activate_task", state)

        tool_name  = state.get("tool_name", "")
        session_id = str(state.get("session_id", ""))

        if tool_name not in _ACTIVATING_TOOLS:
            return {}

        if tool_name == "tasks__set_active":
            task_id = (state.get("tool_input") or {}).get("task_id", "")
            if not task_id:
                _log.warning("[activate_task] tasks__set_active fired but tool_input has no task_id")
                return {}
            current_active = state.get("active_task_id", "")
            stack = list(state.get("task_stack") or [])
            if current_active and current_active != task_id:
                stack.append(current_active)
                _log.info("[activate_task] pushed %s onto stack (depth=%d)", current_active, len(stack))
            updates = _activate(state, task_id, stack)

        else:  # tasks__pop_active
            stack = list(state.get("task_stack") or [])
            if not stack:
                _log.info("[activate_task] pop on empty stack — clearing active task for session=%s", session_id[:8])
                return {
                    "active_task_id": "", "active_task_title": "", "task_body": "",
                    "task_memories": [], "task_stack": [], "mid_task_decisions": [],
                }
            task_id = stack.pop()
            _log.info("[activate_task] popped task=%s from stack (remaining=%d)", task_id, len(stack))
            updates = _activate(state, task_id, stack)

        if not updates:
            return {}

        _log.info(
            "[activate_task] session=%s tool=%s task=%s title=%r memories=%d stack_depth=%d",
            session_id[:8], tool_name, updates.get("active_task_id", ""),
            updates.get("active_task_title", ""),
            len(updates.get("task_memories") or []),
            len(updates.get("task_stack") or []),
        )
        return updates
