"""DecisionTaskNode — PostToolUse node for tasks__add_decision.

Appends the decision text to mid_task_decisions in the checkpoint so it
is injected on every subsequent UPS turn. This is the PostToolUse bridge
that keeps checkpoint writes out of the MCP layer.

Tags: task-decision, post-tool-use, checkpoint, mid-task-decisions
"""
from __future__ import annotations

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class DecisionTaskNode:
    """PostToolUse bridge for tasks__add_decision.

    Reads decision text from tool_input, appends it to mid_task_decisions
    in state so the checkpoint carries it forward. No-ops for other tools.

    Tags: task-decision, post-tool-use, checkpoint, mid-task-decisions
    """

    def __call__(self, state: SessionState) -> dict:
        entry("decision_task", state)

        tool_name = state.get("tool_name", "")
        if tool_name != "tasks__add_decision":
            return {}

        tool_input = state.get("tool_input") or {}
        decision   = str(tool_input.get("decision", "")).strip()
        if not decision:
            return {}

        current  = list(state.get("mid_task_decisions") or [])
        current.append(decision)
        _log.info(
            "[decision_task] session=%s task=%s decisions=%d",
            str(state.get("session_id", ""))[:8],
            str(tool_input.get("task_id", "")),
            len(current),
        )
        return {"mid_task_decisions": current}
