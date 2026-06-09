"""LoadRelatedTasksNode — score done tasks against active task title+tags, return top-3."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_TOP_N = 3
_BODY_SNIPPET_LEN = 200

# Injectable for tests
_TASKS_DB: Path | None = None


def _tasks_db_path() -> Path:
    if _TASKS_DB is not None:
        return _TASKS_DB
    return _cfg.tasks_db


def _tokenise(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    stopwords = {"the", "and", "for", "with", "that", "this", "from", "after", "into", "when", "are", "not", "but"}
    return {t for t in tokens if len(t) >= 4 and t not in stopwords}


class LoadRelatedTasksNode:
    """BM25-style keyword overlap: active task title+tags vs done tasks title+tags+body.

    Tags: related-tasks, BM25, task-injection, done-tasks, keyword-overlap
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_related_tasks", state)

        active_id = state.get("active_task_id", "")
        if not active_id:
            return {"related_tasks": []}

        title = state.get("active_task_title", "")
        query_tokens = _tokenise(title)
        if not query_tokens:
            return {"related_tasks": []}

        db_path = _tasks_db_path()
        if not db_path.exists():
            _log.warning("[load_related_tasks] tasks DB not found: %s", db_path)
            return {"related_tasks": []}

        try:
            with sqlite3.connect(str(db_path)) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT id, title, tags, body FROM open_tasks WHERE status='done' AND id != ?",
                    (active_id,),
                ).fetchall()
        except Exception as exc:
            _log.error("[load_related_tasks] DB error: %s", exc)
            return {"related_tasks": []}

        scored: list[tuple[float, dict]] = []
        for row in rows:
            haystack = f"{row['title'] or ''} {row['tags'] or ''} {row['body'] or ''}".lower()
            haystack_tokens = _tokenise(haystack)
            overlap = len(query_tokens & haystack_tokens)
            if overlap == 0:
                continue
            score = overlap / max(len(query_tokens), 1)
            body = row["body"] or ""
            scored.append((score, {
                "id": row["id"],
                "title": row["title"] or "",
                "body_snippet": body[:_BODY_SNIPPET_LEN].strip(),
                "score": round(score, 3),
            }))

        scored.sort(key=lambda x: -x[0])
        related = [m for _, m in scored[:_TOP_N]]

        _log.info("[load_related_tasks] task=%s candidates=%d returned=%d ids=%s",
                  active_id, len(rows), len(related), [r["id"] for r in related])
        return {"related_tasks": related}
