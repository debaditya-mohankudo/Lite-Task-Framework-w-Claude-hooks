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


class KeywordScoreNode:
    """Score prompt against strong/weak keyword signals from classifier_config.

    Writes classifier_scores (accumulated per-domain int scores) and
    matched_keywords (signal tokens that fired) into state.
    Does not apply threshold — that is apply_threshold's responsibility.
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

        for domain, groups in keyword_signals.items():
            neg = set(negative_signals.get(domain, []))
            if any(n in prompt_lower for n in neg):
                _log.info("[keyword_score] domain=%s skipped (negative signal)", domain)
                continue
            for signal, weight in groups.get("strong", {}).items():
                if (" " in signal and _contains_phrase(prompt_lower, signal)) or signal in tokens:
                    scores[domain] += weight
                    matched.update(signal.split())
            for signal, weight in groups.get("weak", {}).items():
                if (" " in signal and _contains_phrase(prompt_lower, signal)) or signal in tokens:
                    scores[domain] += weight
                    matched.update(signal.split())

        _log.info("[keyword_score] scored %d domains, matched_keywords=%d",
                  len(scores), len(matched))
        return {
            "classifier_scores": dict(scores),
            "matched_keywords":  sorted(matched),
        }
