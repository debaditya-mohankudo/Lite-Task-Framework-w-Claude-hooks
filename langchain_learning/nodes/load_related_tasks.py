"""LoadRelatedTasksNode — find semantically similar done tasks via TurboVec neighbors."""
from __future__ import annotations

import sys
from pathlib import Path

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

# Ensure claude-hooks root is on sys.path so `src.tools.tasks` resolves here,
# not to claude_for_mac_local (which has no handle_neighbors).
_CH_ROOT = str(Path(__file__).resolve().parents[2])
_CH_SRC  = str(Path(__file__).resolve().parents[2] / "src")
for _p in (_CH_ROOT, _CH_SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from src.tools.tasks import handle_neighbors, _TASKS_TVIM  # noqa: E402

_log = get_logger(__name__)

_TOP_N           = 3
_BODY_SNIPPET_LEN = 200


class LoadRelatedTasksNode:
    """Semantic neighbor search: calls handle_neighbors (TurboVec) for the active task.

    Tags: related-tasks, semantic-search, turbovec, task-injection
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_related_tasks", state)
        return {"related_tasks": []}

        # disabled — re-enable by removing the early return above
        active_id = state.get("active_task_id", "")
        if not active_id:
            _log.info("[load_related_tasks] no active task — skipped")
            return {"related_tasks": []}

        if not _TASKS_TVIM.exists():
            _log.info("[load_related_tasks] rag db not created — skipped")
            return {"related_tasks": []}

        try:
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
