"""LoadMemoriesNode — retrieves MEMORY.sqlite rows via combination signal scoring.

Signals per row (all cheap, SQLite-only):
  1. Domain weight   — project domain: 2.0 | global: 0.5 | other: skip
  2. Tag overlap     — Jaccard(prompt_tokens ∩ tag_tokens) × 3.0  (hand-authored, high signal)
  3. Body overlap    — Jaccard(prompt_tokens ∩ body_tokens) × 1.0
  4. Recency boost   — ×1.2 if updated ≤30d, ×0.8 if ≥180d

Global memories are not auto-included — they must earn a slot via keyword overlap.
Tuning: improve tags on memories that surface incorrectly (visible in sqlite logs).
"""
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

_TOP_N              = 5
_SCORED_BATCH_LIMIT = 500

_DOMAIN_WEIGHTS = {
    "project": 2.0,
    "global":  0.5,
}
_TAG_WEIGHT  = 3.0
_BODY_WEIGHT = 1.0


def _recency_multiplier(updated_str: str | None) -> float:
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


def _combination_score(
    row: sqlite3.Row,
    prompt_tokens: set[str],
    project_domain: str | None,
) -> float:
    """Score a memory row against the current prompt using domain + keyword signals."""
    domain = row["domain"] or "global"

    if domain == project_domain:
        domain_weight = _DOMAIN_WEIGHTS["project"]
    elif domain == "global":
        domain_weight = _DOMAIN_WEIGHTS["global"]
    else:
        return 0.0  # out-of-domain — skip entirely

    if not prompt_tokens:
        return domain_weight * _recency_multiplier(row["updated"])

    tag_tokens  = set(tokenise((row["tags"]  or "").lower()))
    body_tokens = set(tokenise((row["body"]  or "").lower()))

    tag_score  = (len(prompt_tokens & tag_tokens)  / max(len(tag_tokens),  1)) * _TAG_WEIGHT
    body_score = (len(prompt_tokens & body_tokens) / max(len(prompt_tokens), 1)) * _BODY_WEIGHT

    return (domain_weight + tag_score + body_score) * _recency_multiplier(row["updated"])


def _score_memories(
    tokens: set[str],
    project_domain: str | None,
    conn: sqlite3.Connection,
) -> list[dict]:
    """Score all candidate memories and return top-N by combination signal."""
    rows = conn.execute(
        "SELECT name, type, domain, tags, body, updated FROM memories LIMIT ?",
        (_SCORED_BATCH_LIMIT,),
    ).fetchall()

    scored: list[tuple[float, dict]] = []
    for row in rows:
        s = _combination_score(row, tokens, project_domain)
        if s > 0:
            scored.append((s, dict(row)))

    scored.sort(key=lambda x: -x[0])
    return [m for _, m in scored[:_TOP_N]]


class LoadMemoriesNode:
    """Retrieve top-5 memories for the current prompt via combination signal scoring.

    Scores every row in MEMORY.sqlite by domain weight + tag overlap + body overlap
    + recency. No embeddings, no external services. Global domain competes on keyword
    relevance — not automatically included.

    Tags: memory, memory-injection, combination-signal, bm25, tag-overlap, prompt-context, MEMORY.sqlite
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_memories", state, prompt_len=len(state.get("prompt", "")))

        prompt = state["prompt"]
        tokens = set(tokenise(prompt.lower()))

        if not _cfg.memory_db.exists():
            _log.warning("[load_memories] MEMORY.sqlite not found at %s", _cfg.memory_db)
            return {"memories": [], "keywords": list(tokens)}

        cwd = state.get("cwd", "")
        project_domain = next(
            (domain for key, domain in _src_cfg.cwd_domain_map.items() if key.lower() in cwd.lower()),
            None,
        )

        try:
            conn = sqlite3.connect(f"file:{_cfg.memory_db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            memories = _score_memories(tokens, project_domain, conn)
            conn.close()
        except Exception as exc:
            _log.error("[load_memories] DB error: %s", exc)
            return {"memories": [], "keywords": list(tokens)}

        names_out = [m.get("name", "?") for m in memories]
        _log.info(
            "[load_memories] mode=combination returned=%d keywords=%d project_domain=%s names=%s",
            len(memories), len(tokens), project_domain, names_out,
        )
        try:
            from hooks.server_memory import record_memories
            record_memories(state.get("session_id", ""), names_out)
        except Exception:
            pass
        return {"memories": memories, "keywords": list(tokens)}
