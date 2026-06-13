"""ActivateTaskNode — PostToolUse node for task activation and stack pop.

Handles:
  tasks__set_active  — reads task_id from tool_input, activates task in checkpoint
  tasks__pop_active  — pops the task_stack and re-activates the previous task

This is the PostToolUse bridge that keeps checkpoint writes out of the MCP layer.

Tags: task-activation, post-tool-use, checkpoint, active-task, task-stack
"""
from __future__ import annotations

from langchain_learning.nodes._node_log import entry
from langchain_learning.nodes.set_active_task import SetActiveTaskNode
from langchain_learning.nodes.load_task_memories import LoadTaskMemoriesNode
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_set_active    = SetActiveTaskNode()
_load_memories = LoadTaskMemoriesNode()

_ACTIVATING_TOOLS = {"tasks__set_active", "tasks__pop_active"}


def _activate(state: SessionState, task_id: str, task_stack: list) -> dict:
    """Run set_active + load_memories for task_id, return merged state updates."""
    activation_state: SessionState = {**state, "active_task_id": task_id, "task_stack": task_stack}  # type: ignore[misc]
    active_updates   = _set_active(activation_state)
    merged_state: SessionState = {**activation_state, **active_updates}  # type: ignore[misc]
    memories_updates = _load_memories(merged_state)
    return {"task_stack": task_stack, **active_updates, **memories_updates}


class ActivateTaskNode:
    """PostToolUse bridge for tasks__set_active and tasks__pop_active.

    tasks__set_active: reads task_id from tool_input, activates task.
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
            updates = _activate(state, task_id, stack)

        else:  # tasks__pop_active
            stack = list(state.get("task_stack") or [])
            if not stack:
                _log.info("[activate_task] pop requested but stack is empty — clearing active task")
                return {
                    "active_task_id": "", "active_task_title": "",
                    "task_memories": [], "task_stack": [], "mid_task_decisions": [],
                }
            task_id = stack.pop()
            updates = _activate(state, task_id, stack)

        _log.info(
            "[activate_task] session=%s tool=%s task=%s title=%r memories=%d stack_depth=%d",
            session_id[:8], tool_name, task_id,
            updates.get("active_task_title", ""),
            len(updates.get("task_memories") or []),
            len(updates.get("task_stack") or []),
        )
        return updates
