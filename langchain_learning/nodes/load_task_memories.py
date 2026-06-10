"""LoadTaskMemoriesNode — scores MEMORY.sqlite against active task tags + title."""
from __future__ import annotations

import sqlite3

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.nodes._text_utils import tokenise
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class LoadTaskMemoriesNode:
    """Score MEMORY.sqlite rows against the active task's title and tags.

    Ranked by overlap with task tokens — deterministic to task content, not
    the current prompt. Returns top-5 as task_memories in state.

    Tags: task-memories, memory-injection, task-activation, MEMORY.sqlite, keyword-overlap
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_task_memories", state)

        task_id    = state.get("active_task_id", "")
        task_title = state.get("active_task_title", "")

        # Tags come in via state if set_active_task stored them; fall back to title only
        tokens = tokenise(task_title)

        if not tokens and not task_id:
            return {"task_memories": []}

        if not _cfg.memory_db.exists():
            _log.warning("[load_task_memories] MEMORY.sqlite not found")
            return {"task_memories": []}

        try:
            conn = sqlite3.connect(f"file:{_cfg.memory_db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT name, type, domain, priority, tags, body FROM memories"
            ).fetchall()
            conn.close()
        except Exception as exc:
            _log.error("[load_task_memories] DB error: %s", exc)
            return {"task_memories": []}

        scored: list[tuple[float, dict]] = []
        for row in rows:
            haystack = f"{row['tags'] or ''} {row['body'] or ''}".lower()
            overlap = sum(1 for t in tokens if t in haystack)
            if overlap > 0:
                scored.append((overlap / max(len(tokens), 1), dict(row)))

        scored.sort(key=lambda x: (-x[0], x[1].get("priority", 50)))
        task_memories = [m for _, m in scored[:5]]
        _log.info("[load_task_memories] task=%s returned=%d", task_id, len(task_memories))
        return {"task_memories": task_memories}
