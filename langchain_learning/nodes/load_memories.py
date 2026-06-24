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

# ---------------------------------------------------------------------------
# Scoring config — loaded from memory_scoring.json (iCloud), mtime-cached
# ---------------------------------------------------------------------------

_scoring_cfg: dict = {}
_scoring_cfg_mtime: float = 0.0

_SCORING_DEFAULTS: dict = {
    "domain_weights": {"project": 2.0, "global": 0.5, "coding-best-practices": 0.3},
    "tag_weight": 3.0,
    "body_weight": 1.0,
    "recency_boost": 1.2,
    "recency_penalty": 0.8,
    "recency_boost_days": 30,
    "recency_penalty_days": 180,
    "top_n": 5,
    "batch_limit": 500,
}


def _load_scoring_cfg() -> dict:
    """Return scoring config, reloading from JSON if file changed since last load."""
    global _scoring_cfg, _scoring_cfg_mtime
    import json as _json
    path = _src_cfg.memory_scoring_json
    try:
        mtime = path.stat().st_mtime
        if mtime != _scoring_cfg_mtime:
            _scoring_cfg = _json.loads(path.read_text())
            _scoring_cfg_mtime = mtime
            _log.info("[load_memories] scoring config reloaded from %s", path.name)
    except Exception:
        if not _scoring_cfg:
            _scoring_cfg = _SCORING_DEFAULTS
    return _scoring_cfg


def _recency_multiplier(updated_str: str | None, cfg: dict) -> float:
    if not updated_str:
        return 1.0
    try:
        updated = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - updated).days
        if age_days <= cfg.get("recency_boost_days", 30):
            return cfg.get("recency_boost", 1.2)
        if age_days >= cfg.get("recency_penalty_days", 180):
            return cfg.get("recency_penalty", 0.8)
        return 1.0
    except Exception:
        return 1.0


def _combination_score(
    row: sqlite3.Row,
    prompt_tokens: set[str],
    project_domain: str | None,
    cfg: dict,
) -> float:
    """Score a memory row against the current prompt using domain + keyword signals.

    Signals:
      1. domain_weight  — project=2.0 | explicit weight per domain | 0 (skip)
      2. keyword_boost  — prompt tokens overlap with domain_keywords in config → +boost
      3. tag_overlap    — Jaccard(prompt ∩ tags) × tag_weight  (primary retrieval lever)
      4. body_overlap   — Jaccard(prompt ∩ body) × body_weight
      5. recency        — multiplier based on updated timestamp
    """
    domain = row["domain"] or "global"
    domain_weights: dict = cfg.get("domain_weights", _SCORING_DEFAULTS["domain_weights"])

    if domain == project_domain:
        domain_weight = domain_weights.get("project", 2.0)
    else:
        domain_weight = domain_weights.get(domain, 0.0)

    # Keyword boost: prompt tokens that signal this domain when CWD didn't match it.
    # Only applied cross-domain — same-domain memories already carry the project weight.
    if prompt_tokens and domain != project_domain:
        domain_kws = set(cfg.get("domain_keywords", {}).get(domain, []))
        if domain_kws and (prompt_tokens & domain_kws):
            domain_weight += cfg.get("domain_keyword_boost", 0.8)

    if domain_weight == 0.0:
        return 0.0  # no domain signal at all — skip

    if not prompt_tokens:
        return domain_weight * _recency_multiplier(row["updated"], cfg)

    tag_tokens  = set(tokenise((row["tags"]  or "").lower()))
    body_tokens = set(tokenise((row["body"]  or "").lower()))

    tag_score  = (len(prompt_tokens & tag_tokens)  / max(len(tag_tokens),  1)) * cfg.get("tag_weight", 3.0)
    body_score = (len(prompt_tokens & body_tokens) / max(len(prompt_tokens), 1)) * cfg.get("body_weight", 1.0)

    # Minimum keyword relevance gate — pure domain-weight floaters don't qualify
    if (tag_score + body_score) < cfg.get("min_keyword_score", 0.0):
        return 0.0

    return (domain_weight + tag_score + body_score) * _recency_multiplier(row["updated"], cfg)


def _score_memories(
    tokens: set[str],
    project_domain: str | None,
    conn: sqlite3.Connection,
) -> list[dict]:
    """Score all candidate memories and return top-N by combination signal."""
    cfg = _load_scoring_cfg()
    rows = conn.execute(
        "SELECT name, type, domain, tags, body, updated FROM memories LIMIT ?",
        (cfg.get("batch_limit", 500),),
    ).fetchall()

    scored: list[tuple[float, dict]] = []
    for row in rows:
        s = _combination_score(row, tokens, project_domain, cfg)
        if s > 0:
            scored.append((s, dict(row)))

    scored.sort(key=lambda x: -x[0])
    return [m for _, m in scored[:cfg.get("top_n", 5)]]


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
