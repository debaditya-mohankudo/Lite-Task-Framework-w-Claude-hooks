"""LoadRelatedTasksNode — find semantically similar done tasks via TurboVec neighbors."""
from __future__ import annotations

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_TOP_N           = 3
_BODY_SNIPPET_LEN = 200


class LoadRelatedTasksNode:
    """Semantic neighbor search: calls handle_neighbors (TurboVec) for the active task.

    Tags: related-tasks, semantic-search, turbovec, task-injection
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_related_tasks", state)

        active_id = state.get("active_task_id", "")
        if not active_id:
            _log.info("[load_related_tasks] no active task — skipped")
            return {"related_tasks": []}

        try:
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
            from tools.tasks import handle_neighbors

            neighbours = handle_neighbors(active_id)
            # Filter to done tasks only and cap at TOP_N
            related = [
                {
                    "id":           n["task_id"],
                    "title":        n["title"],
                    "body_snippet": "",
                    "score":        n["score"],
                }
                for n in neighbours
                if n.get("status") == "done"
            ][:_TOP_N]

        except Exception as exc:
            _log.error("[load_related_tasks] vector search error: %s", exc)
            return {"related_tasks": []}

        _log.info(
            "[load_related_tasks] task=%s returned=%d ids=%s",
            active_id, len(related), [r["id"] for r in related],
        )
        return {"related_tasks": related}
