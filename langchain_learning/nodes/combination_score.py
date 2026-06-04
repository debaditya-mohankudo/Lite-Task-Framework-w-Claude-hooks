"""CombinationScoreNode — adds bonus scores for bigram/trigram combination signals."""
from __future__ import annotations

import re
from collections import defaultdict

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_TOKEN_RE = re.compile(r"\b[\w-]+\b")


class CombinationScoreNode:
    """Apply combination (bigram/trigram) bonus scores on top of keyword_scores.

    Reads state["classifier_scores"] (from keyword_score node), adds combination
    bonuses, and writes the merged scores back. Matched combo tokens are appended
    to matched_keywords.
    """

    def __call__(self, state: SessionState) -> dict:
        entry("combination_score", state)

        prompt  = state.get("prompt", "")
        cfg     = state.get("classifier_config", {})
        scores: dict[str, int] = defaultdict(int, state.get("classifier_scores", {}))
        matched: set[str]      = set(state.get("matched_keywords", []))

        combination_signals: dict = cfg.get("combination_signals", {})
        tokens = set(_TOKEN_RE.findall(prompt.lower()))

        combos_hit = 0
        for domain, combos in combination_signals.items():
            for combo_entry in combos:
                required_words, bonus = set(combo_entry[0]), combo_entry[1]
                if required_words.issubset(tokens):
                    scores[domain] += bonus
                    matched.update(required_words)
                    combos_hit += 1

        _log.info("[combination_score] combos_hit=%d scores=%s",
                  combos_hit, sorted(scores.items(), key=lambda x: -x[1])[:3])
        return {
            "classifier_scores": dict(scores),
            "matched_keywords":  sorted(matched),
        }
