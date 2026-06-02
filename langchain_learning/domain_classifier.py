"""Component 3 — Domain Classification.

LangChain concept: with_structured_output
Replaces: server/domain_classifier.py (keyword engine) + server/core/domain_engine.py (CWD map + orchestration)

Two-stage classification:
  Stage 1 — CWD map: deterministic, no LLM cost.
  Stage 2 — LLM chain: structured output (Pydantic) via Claude Haiku.

The LLM call is optional. Pass use_llm=False (or set LC_DOMAIN_LLM=0) to
skip it and fall back to keyword heuristics only — useful in tests and hooks
that cannot afford an API round-trip.
"""
from __future__ import annotations

from langchain_learning.logger import get_logger
import os
import re
from typing import List, cast

from langchain_anthropic import ChatAnthropic
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, Field

from langchain_learning.config import config as _cfg

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Stage 1 — CWD → domain map (deterministic, no LLM)
# ---------------------------------------------------------------------------

_CWD_DOMAIN_MAP: dict[str, str] = {
    "claude_for_mac_local": "macos",
    "K-mirror":             "philosophy",
    "market-intel":         "market-intel",
    "claude_documents":     "vault",
}

_DEFAULT_DOMAIN = "macos"


def _domain_from_cwd(cwd: str) -> str | None:
    for key, domain in _CWD_DOMAIN_MAP.items():
        if key.lower() in cwd.lower():
            return domain
    return None


# ---------------------------------------------------------------------------
# Stage 2 — LLM structured output
# ---------------------------------------------------------------------------

class DomainClassification(BaseModel):
    """Structured output returned by the domain classifier chain."""

    domains: List[str] = Field(
        description=(
            "List of memory domains most relevant to the prompt. "
            "Pick only from the valid domain list. Return [] if none apply."
        )
    )
    confidence: str = Field(
        description="One of: high | medium | low",
        pattern="^(high|medium|low)$",
    )
    reasoning: str = Field(
        description="One sentence explaining which signals led to these domains.",
    )


_SYSTEM_PROMPT = """\
You are a domain router for a personal AI memory system.
Given a user prompt, identify which memory domains are relevant.

Valid domains:
{valid_domains}

Domain hints:
- astrology: nakshatra, dasha, lagna, rashi, panchang, planetary transits
- philosophy: krishnamurti, consciousness, observer, meditation, thought
- market-intel: nifty, sensex, portfolio, stocks, mutual funds, FII/DII
- vault: saving notes, searching vault, obsidian, documentation
- macos: swift, mcp server, automation, contacts, messages, reminders
- coding-best-practices: python patterns, refactoring, logging, generators
- health: ayurveda, joints, remedies, wellness
- acme: acme_poc, team-memory, code graph
- global: applies to all sessions (do not select this — it is injected automatically)

Return only domains that clearly apply. When in doubt, return fewer domains.
"""

_HUMAN_PROMPT = "Prompt: {prompt}"


def _build_llm_chain():
    llm = ChatAnthropic(model=_cfg.model, temperature=0, max_tokens=256)
    prompt = ChatPromptTemplate.from_messages([
        ("system", _SYSTEM_PROMPT),
        ("human", _HUMAN_PROMPT),
    ])
    structured_llm = llm.with_structured_output(DomainClassification)
    return prompt | structured_llm


# ---------------------------------------------------------------------------
# Keyword fallback — full weighted scoring, ported from server/domain_classifier.py
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"\b[\w-]+\b")

_KEYWORD_SIGNALS: dict[str, dict[str, dict[str, int]]] = {
    "astrology": {
        "strong": {
            "nakshatra": 5, "mahadasha": 5, "antardasha": 5, "navamsa": 4,
            "dashamsha": 4, "lagna": 4, "rashi": 4, "dasha": 4,
            "jaimini": 4, "parashari": 4, "atmakaraka": 4, "kundli": 4, "pancang": 3,
        },
        "weak": {
            "astrology": 2, "chart": 1, "horoscope": 2, "d9": 2, "d10": 2, "planet": 1,
        },
    },
    "philosophy": {
        "strong": {
            "krishnamurti": 5, "conditioning": 4, "consciousness": 4, "observer": 4, "meditation": 3,
        },
        "weak": {
            "thought": 1, "fear": 1, "desire": 1, "ego": 1, "awareness": 1, "self": 1,
        },
    },
    "market-intel": {
        "strong": {
            "nifty": 5, "sensex": 5, "portfolio": 4, "repo rate": 4, "fii": 4, "dii": 4,
        },
        "weak": {
            "market": 1, "stock": 2, "equity": 2, "gold": 2, "gdp": 2,
            "inflation": 2, "rally": 2, "correction": 2, "mutual": 2,
        },
    },
    "vault": {
        "strong": {
            "obsidian": 5, "vault": 5, "save to vault": 5, "search vault": 5,
        },
        "weak": {
            "note": 1, "document": 1, "documentation": 2, "wiki": 1, "capture": 1,
        },
    },
    "macos": {
        "strong": {
            "swift": 5, "macos": 5, "xcode": 5, "launchd": 4, "mcp": 4,
        },
        "weak": {
            "mac": 2, "apple": 1, "terminal": 2, "automation": 2,
            "subprocess": 2, "contact": 2, "phone": 2,
        },
    },
    "session": {
        "strong": {
            "session__": 6, "fastapi": 5, "session_store": 5, "session_db": 5,
            "stopword": 5, "memory_loader": 5,
        },
        "weak": {
            "keyword": 2, "session": 2, "memory": 2, "server": 1, "8765": 3, "ttl": 3, "evict": 3,
        },
    },
    "coding-best-practices": {
        "strong": {
            "pyproject": 5, "sqlite3": 5, "context manager": 4, "observability": 4,
            "docstring": 4, "best practice": 4, "generator": 4, "yield": 4,
        },
        "weak": {
            "python": 2, "logging": 2, "debug": 1, "dependency": 2,
            "package": 1, "import": 1, "refactor": 2,
        },
    },
    "health": {
        "strong": {
            "ayurveda": 5, "ayurvedic": 5, "remedy": 4, "joint": 3, "knee": 3,
        },
        "weak": {
            "herbal": 2, "wellness": 2, "healing": 1,
        },
    },
    "acme": {
        "strong": {
            "acme": 5, "team-memory": 5, "code graph": 4,
        },
        "weak": {
            "chromadb": 2,
        },
    },
    "langchain-learning": {
        "strong": {
            "langchain": 5, "lcel": 5, "langgraph": 5, "stategraph": 5,
            "runnablelambda": 4, "runnableparallel": 4, "baseretriever": 4,
            "ensembleretriever": 4, "bm25": 4,
        },
        "weak": {
            "runnable": 2, "retriever": 2, "pipeline": 1, "chain": 1,
        },
    },
}

_COMBINATION_SIGNALS: dict[str, list[tuple[set[str], int]]] = {
    "astrology": [
        ({"running", "dasha"}, 5), ({"leo", "lagna"}, 3), ({"calculate", "navamsa"}, 5),
        ({"birth", "chart"}, 4), ({"planetary", "transit"}, 4), ({"kundli", "matching"}, 4),
        ({"calculate", "d9"}, 4), ({"calculate", "d10"}, 4),
        ({"panchang", "tarabalam"}, 4), ({"check", "tarabalam"}, 4),
        ({"panchang", "kaalam"}, 4), ({"astro", "chart"}, 4),
    ],
    "macos": [
        ({"swift", "mcp"}, 5), ({"swift", "subprocess"}, 4), ({"macos", "launchd"}, 4),
        ({"xcode", "build"}, 4), ({"local", "llm"}, 3), ({"send", "message"}, 4),
    ],
    "market-intel": [
        ({"portfolio", "deployment"}, 5), ({"gold", "inflation"}, 4), ({"nifty", "bullish"}, 4),
        ({"market", "correction"}, 4), ({"mutual", "fund"}, 5), ({"stock", "valuation"}, 3),
        ({"asset", "allocation"}, 3), ({"gold", "regime"}, 3),
    ],
    "session": [
        ({"session", "keyword"}, 4), ({"session", "memory"}, 4), ({"fastapi", "server"}, 5),
        ({"session", "stopword"}, 5), ({"session", "persist"}, 4), ({"session", "ttl"}, 5),
    ],
    "vault": [
        ({"save", "vault"}, 5), ({"obsidian", "note"}, 4), ({"daily", "note"}, 3),
        ({"search", "vault"}, 4), ({"capture", "session"}, 3),
    ],
    "philosophy": [
        ({"observer", "observed"}, 5), ({"fear", "thought"}, 4),
        ({"psychological", "time"}, 5), ({"self", "root"}, 4),
    ],
    "coding-best-practices": [
        ({"enhance", "code"}, 4), ({"enhance", "feature"}, 4), ({"add", "feature"}, 3),
        ({"refactor", "code"}, 4), ({"write", "script"}, 3), ({"fix", "bug"}, 3),
        ({"split", "loop"}, 5), ({"yield", "loop"}, 5), ({"nested", "loop"}, 4),
        ({"split", "generator"}, 5), ({"add", "function"}, 3), ({"add", "class"}, 3),
        ({"add", "method"}, 3), ({"add", "error", "handling"}, 4),
        ({"create", "function"}, 3), ({"create", "class"}, 3), ({"write", "function"}, 3),
        ({"fix", "function"}, 3), ({"refactor", "function"}, 4), ({"move", "function"}, 3),
        ({"extract", "function"}, 4), ({"simplify", "code"}, 4), ({"clean", "code"}, 4),
        ({"logging", "handler"}, 5), ({"centralized", "logging"}, 5), ({"custom", "handler"}, 4),
        ({"sqlite", "logging"}, 5), ({"log", "handler"}, 4), ({"override", "handler"}, 4),
        ({"inherit", "logging"}, 5), ({"try", "except", "else"}, 5), ({"except", "else"}, 4),
        ({"success", "path"}, 4), ({"silent", "exception"}, 4), ({"swallow", "exception"}, 4),
    ],
    "health": [
        ({"blood", "group"}, 5), ({"mustard", "oil"}, 4), ({"winter", "knees"}, 4),
        ({"joint", "stiffness"}, 4), ({"natural", "healing"}, 4), ({"home", "remedy"}, 4),
    ],
    "acme": [
        ({"team", "memory"}, 5), ({"acme", "poc"}, 5), ({"code", "graph"}, 3),
    ],
    "langchain-learning": [
        ({"langchain", "pipeline"}, 5), ({"lcel", "pipe"}, 5), ({"langgraph", "node"}, 5),
        ({"memory", "retriever"}, 4), ({"tool", "hints"}, 3), ({"domain", "classifier"}, 4),
        ({"build", "pipeline"}, 3), ({"invoke", "chain"}, 4),
    ],
}

_NEGATIVE_SIGNALS: dict[str, set[str]] = {
    "market-intel": {"supermarket", "marketplace"},
}

_CLASSIFY_THRESHOLD = 2


def _contains_phrase(prompt: str, phrase: str) -> bool:
    return phrase.lower() in prompt


def _keyword_classify(prompt: str, threshold: int = _CLASSIFY_THRESHOLD) -> list[str]:
    """Score prompt against weighted keyword + combination signals; return domains above threshold."""
    from collections import defaultdict
    prompt_lower = prompt.lower()
    tokens = set(_TOKEN_RE.findall(prompt_lower))
    scores: dict[str, int] = defaultdict(int)

    for domain, groups in _KEYWORD_SIGNALS.items():
        if any(neg in prompt_lower for neg in _NEGATIVE_SIGNALS.get(domain, set())):
            continue
        for signal, weight in groups.get("strong", {}).items():
            if (" " in signal and _contains_phrase(prompt_lower, signal)) or signal in tokens:
                scores[domain] += weight
        for signal, weight in groups.get("weak", {}).items():
            if (" " in signal and _contains_phrase(prompt_lower, signal)) or signal in tokens:
                scores[domain] += weight

    for domain, combos in _COMBINATION_SIGNALS.items():
        for required_words, bonus in combos:
            if required_words.issubset(tokens):
                scores[domain] += bonus

    return [d for d, s in scores.items() if s >= threshold]


# ---------------------------------------------------------------------------
# Public classifier
# ---------------------------------------------------------------------------

class DomainClassifier:
    """Classifies a prompt into one or more memory domains.

    Two stages:
      1. CWD map — always runs, zero cost.
      2. LLM chain (with_structured_output) — runs when use_llm=True.
         Falls back to keyword heuristics if the LLM call fails.

    LangChain concept taught here: with_structured_output
      - Binds a Pydantic model to the LLM response
      - LLM returns validated, typed data instead of raw text
      - Eliminates manual JSON parsing + validation
    """

    def __init__(self, use_llm: bool | None = None, memory_db=None):
        if use_llm is None:
            use_llm = os.getenv("LC_DOMAIN_LLM", "1") != "0"
        self._use_llm = use_llm
        self._chain = _build_llm_chain() if use_llm else None
        _log.debug("DomainClassifier ready (use_llm=%s)", use_llm)

    def classify(self, prompt: str, cwd: str = "", prior_domains: set[str] | None = None) -> list[str]:
        """Return sorted list of detected domains.

        Args:
            prompt: The user's raw prompt text.
            cwd: Current working directory path — used for deterministic CWD mapping.
            prior_domains: Domains detected in earlier turns (persisted in session).

        Returns:
            Sorted list of domain strings, e.g. ["macos", "vault"].
        """
        domains: set[str] = set(prior_domains or [])

        # Stage 1 — CWD map
        cwd_domain = _domain_from_cwd(cwd)
        if cwd_domain:
            domains.add(cwd_domain)
        elif not domains:
            domains.add(_DEFAULT_DOMAIN)

        # Stage 2 — LLM or keyword fallback
        if self._use_llm and self._chain is not None:
            try:
                result = cast(DomainClassification, self._chain.invoke({
                    "valid_domains": ", ".join(sorted(_cfg.valid_domains - {"global"})),
                    "prompt": prompt,
                }))
                for d in result.domains:
                    if d in _cfg.valid_domains:
                        domains.add(d)
                _log.debug(
                    "LLM classified %s → %s (confidence=%s, reason=%s)",
                    prompt[:60], result.domains, result.confidence, result.reasoning,
                )
            except Exception as exc:
                _log.warning("LLM domain classification failed, falling back to keywords: %s", exc)
                domains.update(_keyword_classify(prompt))
        else:
            domains.update(_keyword_classify(prompt))

        return sorted(domains)


# ---------------------------------------------------------------------------
# Convenience: wrap as a LangChain Runnable for pipeline use (Component 5)
# ---------------------------------------------------------------------------

def make_classifier_runnable(use_llm: bool = False):
    """Return a RunnableLambda that accepts a dict with 'prompt' and 'cwd' keys.

    Used in Component 5 (LCEL pipeline). Default use_llm=False keeps the
    pipeline fast and cost-free for hook use cases.
    """
    classifier = DomainClassifier(use_llm=use_llm)

    def _run(inputs: dict) -> dict:
        domains = classifier.classify(
            prompt=inputs.get("prompt", ""),
            cwd=inputs.get("cwd", ""),
            prior_domains=inputs.get("prior_domains"),
        )
        return {**inputs, "domains": domains}

    return RunnableLambda(_run)
