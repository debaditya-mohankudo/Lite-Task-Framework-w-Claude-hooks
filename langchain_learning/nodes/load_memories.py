"""LoadMemoriesNode — scores MEMORY.sqlite rows against prompt keywords."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.nodes._text_utils import tokenise
from langchain_learning.session_state import SessionState
from src.config import config as _src_cfg
from src.logger import get_logger

_log = get_logger(__name__)

_SCORED_BATCH_LIMIT = 200


def _recency_multiplier(updated_str: str | None) -> float:
    """Return a score multiplier based on how recently the memory was updated.

    ≤30 days → 1.2×  (recent, boost)
    31–179 days → 1.0× (neutral)
    ≥180 days → 0.8× (stale, mild penalty)
    """
    if not updated_str:
        return 1.0
    try:
        updated = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - updated).days
        if age_days <= 30:
            return 1.2
        if age_days >= 180:
            return 0.8
        return 1.0
    except Exception:
        return 1.0


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

        # Infer domain directly from cwd — decoupled from cwd_domain_detect for fan-out parallelization
        cwd = state.get("cwd", "")
        project_domain = next(
            (domain for key, domain in _src_cfg.cwd_domain_map.items() if key.lower() in cwd.lower()),
            None,
        )

        _COLS = "name, type, domain, priority, tags, body, updated"
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
                base = overlap / max(len(tokens), 1)
                score = base * _recency_multiplier(row["updated"])
                scored.append((score, dict(row)))

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
