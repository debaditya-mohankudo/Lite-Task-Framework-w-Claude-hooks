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

from src.logger import get_logger
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
# JSON config loader — reads domain_classifier.json from iCloud
# ---------------------------------------------------------------------------

import json as _json

_DC_JSON_CACHE: dict | None = None


def _load_dc_json() -> dict:
    global _DC_JSON_CACHE
    if _DC_JSON_CACHE is not None:
        return _DC_JSON_CACHE
    try:
        path = _cfg.domain_classifier_json
        with open(path, "r", encoding="utf-8") as f:
            _DC_JSON_CACHE = _json.load(f)
        _log.debug("Loaded domain_classifier.json from %s", path)
    except Exception as exc:
        _log.warning("Could not load domain_classifier.json, using built-in defaults: %s", exc)
        _DC_JSON_CACHE = {}
    return _DC_JSON_CACHE


def _dc(key: str, default):
    return _load_dc_json().get(key, default)


# ---------------------------------------------------------------------------
# Stage 1 — CWD → domain map (deterministic, no LLM)
# ---------------------------------------------------------------------------

_DEFAULT_DOMAIN = "macos"


def _domain_from_cwd(cwd: str) -> str | None:
    cwd_map = _dc("cwd_domain_map", {})
    for key, domain in cwd_map.items():
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


def _get_keyword_signals() -> dict[str, dict[str, dict[str, int]]]:
    return _dc("keyword_signals", {})


def _get_combination_signals() -> dict[str, list[tuple[set[str], int]]]:
    return {
        domain: [(set(words), weight) for words, weight in combos]
        for domain, combos in _dc("combination_signals", {}).items()
    }


def _get_negative_signals() -> dict[str, set[str]]:
    return {domain: set(words) for domain, words in _dc("negative_signals", {}).items()}


def _get_threshold() -> int:
    return _dc("classify_threshold", 2)


def _contains_phrase(prompt: str, phrase: str) -> bool:
    return phrase.lower() in prompt


def _keyword_classify(prompt: str, threshold: int | None = None) -> list[str]:
    """Score prompt against weighted keyword + combination signals; return domains above threshold."""
    from collections import defaultdict
    if threshold is None:
        threshold = _get_threshold()
    keyword_signals = _get_keyword_signals()
    combination_signals = _get_combination_signals()
    negative_signals = _get_negative_signals()

    prompt_lower = prompt.lower()
    tokens = set(_TOKEN_RE.findall(prompt_lower))
    scores: dict[str, int] = defaultdict(int)
    hits: dict[str, list[tuple[str, int]]] = defaultdict(list)

    for domain, groups in keyword_signals.items():
        if any(neg in prompt_lower for neg in negative_signals.get(domain, set())):
            _log.debug("keyword_classify: domain=%r skipped (negative signal match)", domain)
            continue
        for signal, weight in groups.get("strong", {}).items():
            if (" " in signal and _contains_phrase(prompt_lower, signal)) or signal in tokens:
                scores[domain] += weight
                hits[domain].append((signal, weight))
        for signal, weight in groups.get("weak", {}).items():
            if (" " in signal and _contains_phrase(prompt_lower, signal)) or signal in tokens:
                scores[domain] += weight
                hits[domain].append((signal, weight))

    for domain, combos in combination_signals.items():
        for required_words, bonus in combos:
            if required_words.issubset(tokens):
                scores[domain] += bonus
                hits[domain].append(("+".join(sorted(required_words)), bonus))

    if _log.isEnabledFor(10):  # DEBUG
        for domain, score in sorted(scores.items(), key=lambda x: -x[1]):
            _log.debug(
                "keyword_classify: domain=%r score=%d signals=%s",
                domain, score, hits[domain],
            )

    result = [d for d, s in scores.items() if s >= threshold]
    _log.debug("keyword_classify: threshold=%d → matched=%s", threshold, result)
    return result


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
            kw_domains = _keyword_classify(prompt)
            domains.update(kw_domains)
            _log.debug("keyword classified %r → %s", prompt[:60], sorted(kw_domains))

        result = sorted(domains)
        _log.debug("classify final: prompt=%r domains=%s", prompt[:60], result)
        return result


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
