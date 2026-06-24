"""DeactivateTaskNode — PostToolUse node that watches tasks__clear_active and tasks__finish.

When either tool fires, zeros out the active task fields in the checkpoint.
On tasks__finish specifically, injects a structured retrospective prompt via
pending_hook_output so Claude captures decisions, constraints, and patterns
as atomic memories immediately after closing the task.

Tags: task-deactivation, post-tool-use, checkpoint, active-task, retrospective
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

_RETROSPECTIVE_TEMPLATE = """\
## Task retrospective

Task **{title}** (id: {task_id}) just finished. Before moving on, capture what's worth remembering.

Reflect on this task and extract up to 2 atomic memories. For each one that applies, call `memory__add_batch` with:
- `name`: short kebab-case slug
- `type`: feedback | user | project | reference
- `domain`: the relevant domain (e.g. claude-hooks, global)
- `tags`: natural-language keywords that will surface this memory on related future tasks
- `body`: the memory — lead with the rule/fact, then **Why:** and **How to apply:** lines

Focus on what was **non-obvious**: a constraint discovered, a decision made and why, a pattern that worked or failed. Skip if nothing worth keeping surfaced.
"""


class DeactivateTaskNode:
    """PostToolUse bridge for tasks__clear_active and tasks__finish.

    Zeros active task fields in the checkpoint so the next UPS turn
    inherits a clean slate. On tasks__finish, also sets pending_hook_output
    with a retrospective additionalContext prompt. No-ops for any other tool.

    Tags: task-deactivation, post-tool-use, checkpoint, active-task, retrospective
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

        result = dict(_CLEARED_STATE)

        if tool_name == "tasks__finish":
            if not task_id:
                _log.warning("[deactivate_task] tasks__finish fired but tool_input has no task_id — skipping retrospective")
            else:
                title = state.get("active_task_title") or task_id
                retro_prompt = _RETROSPECTIVE_TEMPLATE.format(title=title, task_id=task_id)
                result["pending_hook_output"] = {
                    "hookSpecificOutput": {
                        "hookEventName": "PostToolUse",
                        "additionalContext": retro_prompt,
                    }
                }
                _log.info("[deactivate_task] retrospective injected for task=%s title=%r", task_id, title)

        return result
