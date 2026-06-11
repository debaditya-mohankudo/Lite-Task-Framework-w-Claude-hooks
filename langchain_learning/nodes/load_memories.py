"""LoadMemoriesNode — scores MEMORY.sqlite rows against prompt keywords."""
from __future__ import annotations

import sqlite3

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.nodes._text_utils import tokenise
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_SCORED_BATCH_LIMIT = 200


class LoadMemoriesNode:
    """Score MEMORY.sqlite rows against current prompt keywords.

    Priority-1 and project-domain memories are always included via a direct
    SQL query (no scoring). Remaining rows are fetched with LIMIT and ranked
    by token set intersection overlap.

    Returns top-5 by score, then priority.

    Tags: memory, memory-injection, keyword-overlap, prompt-context, MEMORY.sqlite
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_memories", state, prompt_len=len(state.get("prompt", "")))

        prompt = state["prompt"].lower()
        tokens = tokenise(prompt)

        if not _cfg.memory_db.exists():
            _log.warning("[load_memories] MEMORY.sqlite not found at %s", _cfg.memory_db)
            return {"memories": [], "keywords": list(tokens)}

        project_domain = (state.get("domains") or [None])[0]

        _COLS = "name, type, domain, priority, tags, body"
        always_include: list[dict] = []
        scored: list[tuple[float, dict]] = []

        try:
            conn = sqlite3.connect(f"file:{_cfg.memory_db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row

            # Query 1: priority-1 and project-domain rows — always inject, skip scoring
            if project_domain:
                rows_always = conn.execute(
                    f"SELECT {_COLS} FROM memories WHERE priority = 1 OR domain = ?",
                    (project_domain,),
                ).fetchall()
            else:
                rows_always = conn.execute(
                    f"SELECT {_COLS} FROM memories WHERE priority = 1",
                ).fetchall()
            always_include = [dict(r) for r in rows_always]
            always_names = {r["name"] for r in rows_always}

            # Query 2: remaining rows — scored batch with cap
            if project_domain:
                rows_scored = conn.execute(
                    f"SELECT {_COLS} FROM memories "
                    f"WHERE priority > 1 AND domain != ? "
                    f"LIMIT {_SCORED_BATCH_LIMIT}",
                    (project_domain,),
                ).fetchall()
            else:
                rows_scored = conn.execute(
                    f"SELECT {_COLS} FROM memories "
                    f"WHERE priority > 1 "
                    f"LIMIT {_SCORED_BATCH_LIMIT}",
                ).fetchall()

            conn.close()
        except Exception as exc:
            _log.error("[load_memories] DB error: %s", exc)
            return {"memories": [], "keywords": list(tokens)}

        for row in rows_scored:
            if row["name"] in always_names:
                continue
            haystack = f"{row['tags'] or ''} {row['body'] or ''}".lower()
            memory_tokens = set(tokenise(haystack))
            overlap = len(tokens & memory_tokens)
            if overlap > 0:
                scored.append((overlap / max(len(tokens), 1), dict(row)))

        scored.sort(key=lambda x: (-x[0], x[1].get("priority", 50)))
        top_scored = [m for _, m in scored]

        # Merge: always-include first (sorted by priority), then scored
        always_include.sort(key=lambda m: m.get("priority", 50))
        memories = (always_include + top_scored)[:5]

        names = [m.get("name", "?") for m in memories]
        _log.info(
            "[load_memories] returned=%d always=%d scored_candidates=%d keywords=%d project_domain=%s names=%s",
            len(memories), len(always_include), len(top_scored), len(tokens), project_domain, names,
        )
        return {"memories": memories, "keywords": list(tokens)}
