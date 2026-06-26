"""ActivateTaskNode — PostToolUse node for task activation and stack pop.

Handles:
  tasks__set_active  — reads task_id from tool_input, activates task in checkpoint
  tasks__pop_active  — pops the task_stack and re-activates the previous task

DB logic is inlined (not delegated to SetActiveTaskNode/LoadTaskMemoriesNode) so
those nodes' entry() calls don't pollute the PostToolUse log stream with wrong event context.

Tags: task-activation, post-tool-use, checkpoint, active-task, task-stack
"""
from __future__ import annotations

import re
import sqlite3

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._memory_scoring import score_memories
from langchain_learning.nodes._node_log import entry
from langchain_learning.nodes._text_utils import tokenise, task_project_tag
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_ACTIVATING_TOOLS = {"tasks__set_active", "tasks__pop_active"}


def _lookup_task(task_id: str) -> tuple[str, str, str, str] | None:
    """Return (title, body, parent_id, parent_title) for task_id, marking open→active. None if not found."""
    if not _cfg.tasks_db.exists():
        return None
    try:
        with sqlite3.connect(str(_cfg.tasks_db), timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT title, body, status, parent_id FROM open_tasks WHERE id = ?", (task_id,)
            ).fetchone()
            if row is None:
                return None
            parent_id, parent_title = lookup_parent_task(conn, row)
    except Exception as exc:
        _log.error("[activate_task] DB error looking up task=%s: %s", task_id, exc)
        return None

    if row["status"] == "open":
        from src.tools.tasks import handle_update
        result = handle_update(id=task_id, status="active")
        if "error" in result:
            _log.warning("[activate_task] open→active transition blocked task=%s: %s", task_id, result["error"])

    return row["title"], row["body"] or "", parent_id, parent_title

def lookup_parent_task(conn, row):
    parent_id = row["parent_id"] or ""
    parent_title = ""
    if parent_id:
        p = conn.execute(
                    "SELECT title FROM open_tasks WHERE id = ?", (parent_id,)
                ).fetchone()
        if p:
            parent_title = p["title"]
    return parent_id,parent_title


def _score_memories(task_id: str, task_title: str, task_body: str = "") -> list[dict]:
    """Score MEMORY.sqlite rows against task title + body using combination signals."""
    tokens = set(tokenise(f"{task_title} {task_body}".lower()))
    if not tokens or not _cfg.memory_db.exists():
        return []
    project_domain = task_project_tag(task_id, _cfg.tasks_db)
    try:
        conn = sqlite3.connect(f"file:{_cfg.memory_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        memories = score_memories(tokens, project_domain, conn)
        conn.close()
    except Exception as exc:
        _log.warning("[activate_task] memory DB error: %s", exc)
        return []
    return memories


def _parse_files_section(body: str) -> list[str]:
    """Extract comma-separated paths from the 'Files:' section of a task body."""
    m = re.search(r"^Files:\s*\n(.*?)(?=\n[A-Z][a-z]+:|\Z)", body, re.MULTILINE | re.DOTALL)
    if not m:
        return []
    raw = m.group(1).strip()
    return [p.strip() for p in re.split(r"[,\n]+", raw) if p.strip()]


def _file_tokens(paths: list[str]) -> set[str]:
    """Turn file stems into match tokens. hooks/gates.py → {gate, gates}.

    Only stems are used — directory names (e.g. 'hooks') are too generic
    and would match memory slugs containing 'claude-hooks' incorrectly.
    """
    tokens: set[str] = set()
    for path in paths:
        stem = re.sub(r"\.[a-z]+$", "", path.split("/")[-1])
        parts = re.split(r"[_\-]", stem)
        for part in parts:
            t = part.lower()
            if len(t) >= 3:
                tokens.add(t)
                if t.endswith("s") and len(t) > 4:
                    tokens.add(t[:-1])
                else:
                    tokens.add(t + "s")
    return tokens


def _backfill_memory_files(task_id: str, task_body: str, domain: str) -> int:
    """Update files=NULL memories whose name/tags overlap with task Files: section tokens.

    Returns count of memories updated.
    """
    if not domain or not _cfg.memory_db.exists():
        return 0

    file_paths = _parse_files_section(task_body)
    if not file_paths:
        return 0

    file_tok = _file_tokens(file_paths)
    if not file_tok:
        return 0

    files_value = ", ".join(file_paths)

    try:
        with sqlite3.connect(str(_cfg.memory_db), timeout=5) as conn:
            rows = conn.execute(
                """
                SELECT name, tags FROM memories
                WHERE files IS NULL AND domain = ?
                ORDER BY COALESCE(last_validated, updated) ASC
                LIMIT 10
                """,
                (domain,),
            ).fetchall()

            updated = 0
            for name, tags in rows:
                mem_tok = tokenise(f"{name} {tags or ''}")
                if mem_tok & file_tok:
                    conn.execute(
                        "UPDATE memories SET files = ? WHERE name = ?",
                        (files_value, name),
                    )
                    _log.info(
                        "[activate_task] backfilled files for memory=%s domain=%s files=%r",
                        name, domain, files_value,
                    )
                    updated += 1

        return updated
    except Exception as exc:
        _log.warning("[activate_task] files backfill error task=%s: %s", task_id, exc)
        return 0


def _activate(state: SessionState, task_id: str, task_stack: list) -> dict:
    """Resolve task from DB + score memories. Returns state update dict."""
    result = _lookup_task(task_id)
    if result is None:
        _log.warning("[activate_task] task_id=%s not found in proj_tasks.db", task_id)
        return {}
    title, body, parent_id, parent_title = result
    memories = _score_memories(task_id, title, body)

    domain = task_project_tag(task_id, _cfg.tasks_db) or "global"
    backfilled = _backfill_memory_files(task_id, body, domain)
    if backfilled:
        _log.info("[activate_task] backfilled files for %d memories domain=%s", backfilled, domain)

    return {
        "active_task_id":           task_id,
        "active_task_title":        title,
        "task_body":                body,
        "task_memories":            memories,
        "task_stack":               task_stack,
        "active_parent_task_id":    parent_id,
        "active_parent_task_title": parent_title,
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
