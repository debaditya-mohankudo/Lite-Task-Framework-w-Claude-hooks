"""KeywordScoreNode — scores strong/weak keyword signals against the prompt."""
from __future__ import annotations

import re
from collections import defaultdict

from langchain_learning.nodes._node_log import entry
from langchain_learning.nodes.load_classifier_config import get_classifier_config
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_TOKEN_RE = re.compile(r"\b[\w-]+\b")


def _contains_phrase(prompt_lower: str, phrase: str) -> bool:
    return phrase.lower() in prompt_lower


def _iter_domains(keyword_signals: dict, negative_signals: dict, prompt_lower: str):
    """Yield (domain, groups) skipping domains blocked by a negative signal."""
    for domain, groups in keyword_signals.items():
        neg = set(negative_signals.get(domain, []))
        if any(n in prompt_lower for n in neg):
            _log.info("[keyword_score] domain=%s skipped (negative signal)", domain)
            continue
        yield domain, groups


def _score_domain(groups: dict, prompt_lower: str, tokens: set[str]) -> tuple[int, set[str]]:
    """Return (total_score, matched_tokens) for one domain's signal groups."""
    score = 0
    matched: set[str] = set()
    for signal, weight in [*groups.get("strong", {}).items(), *groups.get("weak", {}).items()]:
        if (" " in signal and _contains_phrase(prompt_lower, signal)) or signal in tokens:
            score += weight
            matched.update(signal.split())
    return score, matched


class KeywordScoreNode:
    """Score prompt against strong/weak keyword signals from classifier_config.

    Writes classifier_scores (accumulated per-domain int scores) and
    matched_keywords (signal tokens that fired) into state.
    Does not apply threshold — that is apply_threshold's responsibility.

    Not true BM25 — no IDF or length normalization. Keyword intersection with
    integer weights from domain_classifier.json.

    Example — prompt: "send a message to Alice about the meeting"
      tokens = {"send", "message", "alice", "meeting", ...}
      macos strong: {"send": 5, "message": 3} → score=8, matched={"send","message"}
      vault strong: {"meeting": 3}            → score=3, matched={"meeting"}
      apply_threshold later picks macos (vault score below threshold).

    Tags: scoring-pipeline, keyword-signal, classifier-scores, domain-classification
    """

    def __call__(self, state: SessionState) -> dict:
        entry("keyword_score", state)

        prompt = state.get("prompt", "")
        cfg    = get_classifier_config()

        keyword_signals: dict  = cfg.get("keyword_signals", {})
        negative_signals: dict = cfg.get("negative_signals", {})

        prompt_lower = prompt.lower()
        tokens       = set(_TOKEN_RE.findall(prompt_lower))
        scores: dict[str, int] = defaultdict(int)
        matched: set[str]      = set()

        for domain, groups in _iter_domains(keyword_signals, negative_signals, prompt_lower):
            score, domain_matched = _score_domain(groups, prompt_lower, tokens)
            if score:
                scores[domain] += score
                matched.update(domain_matched)

        _log.info("[keyword_score] scored %d domains, matched_keywords=%d",
                  len(scores), len(matched))
        return {
            "classifier_scores": dict(scores),
            "matched_keywords":  sorted(matched),
        }
