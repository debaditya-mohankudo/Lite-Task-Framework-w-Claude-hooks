"""Shared combination-signal scorer for memory retrieval.

Used by LoadMemoriesNode (prompt → memories) and ActivateTaskNode (task → memories).

Signals per row:
  1. domain_weight  — project=2.0 | explicit weight per domain | 0 (skip)
  2. keyword_boost  — prompt tokens overlap with domain_keywords → +boost (cross-domain only)
  3. tag_overlap    — Jaccard(tokens ∩ tags) × tag_weight
  4. body_overlap   — Jaccard(tokens ∩ body) × body_weight
  5. recency        — multiplier based on updated timestamp

Config loaded from memory_scoring.json (iCloud), mtime-cached.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from langchain_learning.nodes._text_utils import tokenise
from src.config import config as _src_cfg
from src.logger import get_logger

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Config — mtime-cached
# ---------------------------------------------------------------------------

_scoring_cfg: dict = {}
_scoring_cfg_mtime: float = 0.0

SCORING_DEFAULTS: dict = {
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


def load_scoring_cfg() -> dict:
    """Return scoring config, reloading from JSON if file changed since last load."""
    global _scoring_cfg, _scoring_cfg_mtime
    path = _src_cfg.memory_scoring_json
    try:
        mtime = path.stat().st_mtime
        if mtime != _scoring_cfg_mtime:
            _scoring_cfg = json.loads(path.read_text())
            _scoring_cfg_mtime = mtime
            _log.info("[memory_scoring] config reloaded from %s", path.name)
    except Exception:
        if not _scoring_cfg:
            _scoring_cfg = SCORING_DEFAULTS
    return _scoring_cfg


# ---------------------------------------------------------------------------
# Scoring primitives
# ---------------------------------------------------------------------------

def recency_multiplier(updated_str: str | None, cfg: dict) -> float:
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


def combination_score(
    row: sqlite3.Row,
    tokens: set[str],
    project_domain: str | None,
    cfg: dict,
) -> float:
    """Score one memory row against a token set using combination signals."""
    domain = row["domain"] or "global"
    domain_weights: dict = cfg.get("domain_weights", SCORING_DEFAULTS["domain_weights"])

    if domain == project_domain:
        domain_weight = domain_weights.get("project", 2.0)
    else:
        domain_weight = domain_weights.get(domain, 0.0)

    # Cross-domain keyword boost — only when CWD didn't already match this domain
    if tokens and domain != project_domain:
        domain_kws = set(cfg.get("domain_keywords", {}).get(domain, []))
        if domain_kws and (tokens & domain_kws):
            domain_weight += cfg.get("domain_keyword_boost", 0.8)

    # Combination signals — pairs of tokens that together boost a domain
    for combo, bonus in cfg.get("combination_signals", {}).get(domain, []):
        if set(combo).issubset(tokens):
            domain_weight += bonus

    if domain_weight == 0.0:
        return 0.0

    if not tokens:
        return domain_weight * recency_multiplier(row["updated"], cfg)

    tag_tokens  = set(tokenise((row["tags"]  or "").lower()))
    body_tokens = set(tokenise((row["body"]  or "").lower()))

    tag_score  = (len(tokens & tag_tokens)  / max(len(tag_tokens),  1)) * cfg.get("tag_weight", 3.0)
    body_score = (len(tokens & body_tokens) / max(len(tokens), 1))      * cfg.get("body_weight", 1.0)

    if (tag_score + body_score) < cfg.get("min_keyword_score", 0.0):
        return 0.0

    return (domain_weight + tag_score + body_score) * recency_multiplier(row["updated"], cfg)


def score_memories(
    tokens: set[str],
    project_domain: str | None,
    conn: sqlite3.Connection,
    top_n: int | None = None,
) -> list[dict]:
    """Score all candidate memories and return top-N by combination signal."""
    cfg = load_scoring_cfg()
    rows = conn.execute(
        "SELECT name, type, domain, tags, body, updated FROM memories LIMIT ?",
        (cfg.get("batch_limit", 500),),
    ).fetchall()

    scored: list[tuple[float, dict]] = []
    for row in rows:
        s = combination_score(row, tokens, project_domain, cfg)
        if s > 0:
            scored.append((s, dict(row)))

    scored.sort(key=lambda x: -x[0])
    n = top_n if top_n is not None else cfg.get("top_n", 5)
    return [m for _, m in scored[:n]]
