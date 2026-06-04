"""ClassifyDomainNode — detects active domains from keyword + memory signals."""
from __future__ import annotations

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_DOMAIN_VOCAB: dict[str, set[str]] = {
    "astrology":             {"nakshatra", "panchang", "rahu", "ketu", "dasha", "tithi", "lagna", "graha", "jyotish"},
    "market-intel":          {"gold", "nifty", "sensex", "fii", "dii", "market", "stock", "equity", "portfolio"},
    "vault":                 {"vault", "note", "write", "document", "save", "capture"},
    "macos":                 {"message", "calendar", "contact", "reminder", "mail", "imessage", "safari", "music"},
    "health":                {"health", "sleep", "exercise", "weight", "calories", "heart"},
    "philosophy":            {"philosophy", "vedanta", "advaita", "consciousness", "brahman"},
    "coding-best-practices": {"python", "code", "function", "class", "test", "async", "typing"},
}


class ClassifyDomainNode:
    """Detect which domains are active from keyword overlap and top memory domains.

    Sets skip_tools=True when no domain is detected, causing the graph to skip
    score_tools and go straight to persist_session.
    """

    def __call__(self, state: SessionState) -> dict:
        entry("classify_domain", state, memories=len(state.get("memories", [])))

        keywords = set(state["keywords"])
        memories = state["memories"]

        detected: set[str] = set()
        for domain, vocab in _DOMAIN_VOCAB.items():
            if keywords & vocab:
                detected.add(domain)
        for mem in memories[:3]:
            d = mem.get("domain", "global")
            if d and d != "global" and d in _cfg.valid_domains:
                detected.add(d)

        domains = sorted(detected)
        _log.info("[classify_domain] domains=%s skip_tools=%s", domains, not domains)
        return {"domains": domains, "skip_tools": not domains}
