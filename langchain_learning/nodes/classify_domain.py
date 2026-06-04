"""ClassifyDomainNode — detects active domains from weighted keyword signals + CWD map."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_TOKEN_RE = re.compile(r"\b[\w-]+\b")

# ---------------------------------------------------------------------------
# JSON config — loaded once, cached
# ---------------------------------------------------------------------------

_DC_CACHE: dict | None = None


def _load_dc() -> dict:
    global _DC_CACHE
    if _DC_CACHE is not None:
        return _DC_CACHE
    try:
        from src.config import config as src_cfg
        path: Path = src_cfg.domain_classifier_json
        with open(path, "r", encoding="utf-8") as f:
            _DC_CACHE = json.load(f)
        _log.debug("Loaded domain_classifier.json from %s", path)
    except Exception as exc:
        _log.warning("Could not load domain_classifier.json: %s", exc)
        _DC_CACHE = {}
    return _DC_CACHE


def _dc(key: str, default):
    return _load_dc().get(key, default)


# ---------------------------------------------------------------------------
# Scoring helpers — ported from langchain_learning/domain_classifier.py
# ---------------------------------------------------------------------------

def _contains_phrase(prompt_lower: str, phrase: str) -> bool:
    return phrase.lower() in prompt_lower


def _keyword_classify(prompt: str) -> tuple[list[str], dict[str, int], set[str]]:
    """Score prompt against weighted keyword + combination signals.

    Returns:
        domains: list of domains that crossed the threshold
        scores:  per-domain raw score (for logging)
        matched_keywords: all signal tokens that matched (for keywords enrichment)
    """
    threshold: int = _dc("classify_threshold", 2)
    keyword_signals: dict = _dc("keyword_signals", {})
    combination_signals: dict = _dc("combination_signals", {})
    negative_signals: dict = _dc("negative_signals", {})

    prompt_lower = prompt.lower()
    tokens = set(_TOKEN_RE.findall(prompt_lower))
    scores: dict[str, int] = defaultdict(int)
    matched: set[str] = set()

    for domain, groups in keyword_signals.items():
        neg = set(negative_signals.get(domain, []))
        if any(n in prompt_lower for n in neg):
            continue
        for signal, weight in groups.get("strong", {}).items():
            if (" " in signal and _contains_phrase(prompt_lower, signal)) or signal in tokens:
                scores[domain] += weight
                matched.update(signal.split())
        for signal, weight in groups.get("weak", {}).items():
            if (" " in signal and _contains_phrase(prompt_lower, signal)) or signal in tokens:
                scores[domain] += weight
                matched.update(signal.split())

    for domain, combos in combination_signals.items():
        for entry_combo in combos:
            required_words, bonus = set(entry_combo[0]), entry_combo[1]
            if required_words.issubset(tokens):
                scores[domain] += bonus
                matched.update(required_words)

    domains = [d for d, s in scores.items() if s >= threshold]
    return domains, dict(scores), matched


def _cwd_domain(cwd: str) -> str | None:
    cwd_map: dict = _dc("cwd_domain_map", {})
    for key, domain in cwd_map.items():
        if key.lower() in cwd.lower():
            return domain
    return None


# ---------------------------------------------------------------------------
# Node
# ---------------------------------------------------------------------------

class ClassifyDomainNode:
    """Detect which domains are active from weighted signals and top memory domains.

    Sets skip_tools=True when no domain is detected, causing the graph to skip
    score_tools and go straight to persist_session.
    """

    def __call__(self, state: SessionState) -> dict:
        entry("classify_domain", state, memories=len(state.get("memories", [])))

        prompt   = state.get("prompt", "")
        cwd      = state.get("cwd", "")
        memories = state.get("memories", [])
        existing_keywords: list[str] = list(state.get("keywords", []))

        detected: set[str] = set()

        # CWD map — deterministic, from state (not os.getcwd())
        cwd_d = _cwd_domain(cwd)
        if cwd_d:
            detected.add(cwd_d)

        # Weighted keyword scoring
        kw_domains, scores, matched_keywords = _keyword_classify(prompt)
        detected.update(kw_domains)

        # Memory domain signal (top-3 injected memories)
        for mem in memories[:3]:
            d = mem.get("domain", "global")
            if d and d != "global" and d in _cfg.valid_domains:
                detected.add(d)

        domains = sorted(detected)

        # Log top scoring domains for observability
        top_scores = sorted(scores.items(), key=lambda x: -x[1])[:3]
        _log.info("[classify_domain] domains=%s skip_tools=%s top_scores=%s",
                  domains, not domains, top_scores)

        # Enrich keywords with matched signal tokens for downstream BM25
        enriched = sorted(set(existing_keywords) | matched_keywords)

        return {"domains": domains, "skip_tools": not domains, "keywords": enriched}
