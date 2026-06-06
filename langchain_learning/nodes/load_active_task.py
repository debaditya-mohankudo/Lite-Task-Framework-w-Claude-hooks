"""LoadActiveTaskNode — reads active_task_id from checkpoint state (no DB lookup)."""
from __future__ import annotations

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class LoadActiveTaskNode:
    """Pass-through node — active_task_id already lives in checkpoint state,
    written by the task_activate branch (task_graph.py).

    No DB lookup, no scoring. Just logs presence for observability.
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_active_task", state)
        task_id = state.get("active_task_id", "")
        if task_id:
            _log.info("[load_active_task] session=%s active_task=%s title=%r",
                      (state.get("session_id") or "")[:8], task_id,
                      state.get("active_task_title", ""))
        return {}
