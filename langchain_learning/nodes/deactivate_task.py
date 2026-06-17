"""DeactivateTaskNode — PostToolUse node that watches tasks__clear_active and tasks__finish.

When either tool fires, zeros out the active task fields in the checkpoint.
This is the PostToolUse bridge that keeps checkpoint writes out of the MCP layer.

Tags: task-deactivation, post-tool-use, checkpoint, active-task
"""
from __future__ import annotations

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_DEACTIVATING_TOOLS = {"tasks__clear_active", "tasks__finish"}

_CLEARED_STATE = {
    "active_task_id":           "",
    "active_task_title":        "",
    "active_parent_task_id":    "",
    "active_parent_task_title": "",
    "task_memories":            [],
    "task_stack":               [],
    "mid_task_decisions":       [],
}


class DeactivateTaskNode:
    """PostToolUse bridge for tasks__clear_active and tasks__finish.

    Zeros active task fields in the checkpoint so the next UPS turn
    inherits a clean slate. No-ops for any other tool name.

    Tags: task-deactivation, post-tool-use, checkpoint, active-task
    """

    def __call__(self, state: SessionState) -> dict:
        entry("deactivate_task", state)

        tool_name = state.get("tool_name", "")
        if tool_name not in _DEACTIVATING_TOOLS:
            return {}

        session_id = str(state.get("session_id", ""))
        task_id    = (state.get("tool_input") or {}).get("task_id", "")
        _log.info(
            "[deactivate_task] session=%s tool=%s task=%s — clearing checkpoint",
            session_id[:8], tool_name, task_id or "n/a",
        )
        return _CLEARED_STATE
