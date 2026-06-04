"""LoadMemoriesNode — scores MEMORY.sqlite rows against prompt keywords."""
from __future__ import annotations

import re
import sqlite3

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


def _tokenise(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z]{3,}", text.lower()) if t]


class LoadMemoriesNode:
    """Score MEMORY.sqlite rows against current prompt keywords.

    Priority-1 memories are always included. Others ranked by keyword overlap.
    Returns top-10 by score, then priority.
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_memories", state, prompt_len=len(state.get("prompt", "")))

        prompt = state["prompt"].lower()
        tokens = set(_tokenise(prompt))

        if not _cfg.memory_db.exists():
            _log.warning("[load_memories] MEMORY.sqlite not found at %s", _cfg.memory_db)
            return {"memories": [], "keywords": list(tokens)}

        scored: list[tuple[float, dict]] = []
        try:
            conn = sqlite3.connect(f"file:{_cfg.memory_db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT name, type, domain, priority, tags, body FROM memories"
            ).fetchall()
            conn.close()
        except Exception as exc:
            _log.error("[load_memories] DB error: %s", exc)
            return {"memories": [], "keywords": list(tokens)}

        for row in rows:
            if row["priority"] == 1:
                scored.append((1.0, dict(row)))
                continue
            haystack = f"{row['tags'] or ''} {row['body'] or ''}".lower()
            overlap = sum(1 for t in tokens if t in haystack)
            if overlap > 0:
                scored.append((overlap / max(len(tokens), 1), dict(row)))

        scored.sort(key=lambda x: (-x[0], x[1].get("priority", 50)))
        memories = [m for _, m in scored[:10]]
        _log.info("[load_memories] returned=%d keywords=%d", len(memories), len(tokens))
        return {"memories": memories, "keywords": list(tokens)}
