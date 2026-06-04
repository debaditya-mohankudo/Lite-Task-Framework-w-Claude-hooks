"""ApplyThresholdNode — filters classifier_scores by threshold, sets domains + skip_tools."""
from __future__ import annotations

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class ApplyThresholdNode:
    """Gate: domains whose score >= threshold are active. Sets skip_tools if none pass.

    Also merges matched_keywords into state["keywords"] to enrich downstream
    BM25 scoring in ScoreToolsNode.

    This is the only node that writes skip_tools — making it the single decision
    point for the score_tools vs persist_session conditional edge.
    """

    def __call__(self, state: SessionState) -> dict:
        entry("apply_threshold", state)

        cfg       = state.get("classifier_config", {})
        scores    = state.get("classifier_scores", {})
        threshold = cfg.get("classify_threshold", 2)

        # Domains from scoring that crossed threshold
        scored_domains = [d for d, s in scores.items() if s >= threshold]

        # Fall back to default_domain when nothing scored from keywords/combos
        if not scored_domains:
            default = cfg.get("default_domain", "macos")
            scored_domains = [default]

        # Merge with domains already in state (from cwd_domain_detect + memory_domain_signal)
        existing = list(state.get("domains", []))
        all_domains = sorted(set(existing) | set(scored_domains))

        # Enrich keywords with matched signal tokens
        existing_kw  = list(state.get("keywords", []))
        matched      = list(state.get("matched_keywords", []))
        enriched_kw  = sorted(set(existing_kw) | set(matched))

        skip_tools = not all_domains

        # Log top-3 scores for observability
        top_scores = sorted(scores.items(), key=lambda x: -x[1])[:3]
        _log.info("[apply_threshold] threshold=%d domains=%s skip_tools=%s top_scores=%s",
                  threshold, all_domains, skip_tools, top_scores)

        return {
            "domains":    all_domains,
            "skip_tools": skip_tools,
            "keywords":   enriched_kw,
        }
