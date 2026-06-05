"""LoadOpenTasksNode — injects matching open/wip tasks into session state."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_TASKS_DB = Path.home() / ".claude" / "proj_tasks.db"

# Matches explicit task pin: "task:abc12345" anywhere in the prompt
_TASK_PIN_RE = re.compile(r"\btask:([0-9a-f]{8})\b", re.IGNORECASE)


class LoadOpenTasksNode:
    """Score proj_tasks.db open/wip tasks against current prompt keywords.

    Pinned tasks (task:<id> in prompt) are always included regardless of score.
    Returns top-5 matching tasks as open_tasks list in state.
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_open_tasks", state)

        keywords = set(state.get("keywords") or [])
        prompt   = state.get("prompt", "")

        # Explicit task pins from prompt text
        pinned_ids = {m.group(1).lower() for m in _TASK_PIN_RE.finditer(prompt)}

        if not _TASKS_DB.exists():
            return {"open_tasks": []}

        try:
            with sqlite3.connect(f"file:{_TASKS_DB}?mode=ro", uri=True) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT id, title, body, tags, status FROM open_tasks"
                    " WHERE status IN ('open', 'wip')"
                ).fetchall()
                event_counts: dict[str, int] = {
                    r["task_id"]: r["cnt"]
                    for r in conn.execute(
                        "SELECT task_id, COUNT(*) as cnt FROM task_events GROUP BY task_id"
                    ).fetchall()
                }
        except Exception as exc:
            _log.error("[load_open_tasks] DB error: %s", exc)
            return {"open_tasks": []}

        scored: list[tuple[float, dict]] = []
        for row in rows:
            task = dict(row)
            task["event_count"] = event_counts.get(task["id"], 0)
            if task["id"] in pinned_ids:
                task["_pinned"] = True
                scored.append((2.0, task))  # pin beats any keyword score
                continue
            if not keywords:
                continue
            haystack = f"{task['title']} {task['body']} {task['tags']}".lower()
            hits = sum(1 for k in keywords if k in haystack)
            if hits > 0:
                task["_pinned"] = False
                scored.append((hits / len(keywords), task))

        scored.sort(key=lambda x: -x[0])
        tasks = [t for _, t in scored[:5]]
        _log.info("[load_open_tasks] returned=%d pinned=%d", len(tasks), len(pinned_ids))
        return {"open_tasks": tasks}
