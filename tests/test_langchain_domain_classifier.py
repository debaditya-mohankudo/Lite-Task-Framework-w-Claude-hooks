"""Tests for Component 3 — DomainClassifier.

All tests run with use_llm=False (no API calls).
LLM path is tested structurally (chain is built, DomainClassification schema is valid).
"""
import pytest

from langchain_learning.domain_classifier import (
    DomainClassifier,
    DomainClassification,
    _domain_from_cwd,
    _keyword_classify,
    make_classifier_runnable,
)
from langchain_learning.config import Config
_cfg = Config()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
# Stage 1 — CWD map
# ---------------------------------------------------------------------------

def test_cwd_map_macos():
    assert _domain_from_cwd("/Users/x/workspace/claude_for_mac_local") == "macos"


def test_cwd_map_philosophy():
    assert _domain_from_cwd("/Users/x/workspace/K-mirror") == "philosophy"


def test_cwd_map_market():
    assert _domain_from_cwd("/Users/x/workspace/market-intel") == "market-intel"


def test_cwd_map_vault():
    assert _domain_from_cwd("/Users/x/workspace/claude_documents") == "vault"


def test_cwd_map_unknown_returns_none():
    assert _domain_from_cwd("/Users/x/workspace/unknown-project") is None


# ---------------------------------------------------------------------------
# Keyword fallback
# ---------------------------------------------------------------------------

def test_keyword_classify_astrology():
    domains = _keyword_classify("running dasha for leo lagna")
    assert "astrology" in domains


def test_keyword_classify_market():
    domains = _keyword_classify("check nifty and portfolio today")
    assert "market-intel" in domains


def test_keyword_classify_vault():
    domains = _keyword_classify("save this to vault obsidian")
    assert "vault" in domains


def test_keyword_classify_no_match():
    domains = _keyword_classify("hello world completely random")
    assert domains == []


def test_keyword_classify_multi_domain():
    domains = _keyword_classify("langchain refactor with macos automation")
    assert "coding-best-practices" in domains
    assert "macos" in domains


# ---------------------------------------------------------------------------
# DomainClassifier (no LLM)
# ---------------------------------------------------------------------------

@pytest.fixture
def clf():
    return DomainClassifier(use_llm=False)


def test_classifier_uses_cwd_domain(clf):
    domains = clf.classify("tell me something", cwd="/Users/x/workspace/K-mirror")
    assert "philosophy" in domains


def test_classifier_defaults_to_macos_when_no_cwd(clf):
    domains = clf.classify("some random prompt", cwd="")
    assert "macos" in domains


def test_classifier_adds_keyword_domains(clf):
    domains = clf.classify("running dasha for nakshatra", cwd="")
    assert "astrology" in domains


def test_classifier_merges_prior_domains(clf):
    domains = clf.classify("new turn prompt", cwd="", prior_domains={"vault"})
    assert "vault" in domains


def test_classifier_all_returned_domains_are_valid(clf):
    # Classifier may return domains from MEMORY.sqlite not in the static VALID_DOMAINS frozenset.
    # What matters: output is a list of non-empty strings.
    domains = clf.classify("nifty lagna macos refactor vault", cwd="")
    assert all(isinstance(d, str) and d for d in domains)


def test_classifier_returns_sorted(clf):
    domains = clf.classify("nifty nakshatra vault", cwd="")
    assert domains == sorted(domains)


def test_classifier_no_duplicate_domains(clf):
    # cwd gives "macos" AND keyword gives "macos" — should appear once
    domains = clf.classify("macos automation", cwd="/Users/x/workspace/claude_for_mac_local")
    assert domains.count("macos") == 1


# ---------------------------------------------------------------------------
# DomainClassification schema (Pydantic model — LLM structured output contract)
# ---------------------------------------------------------------------------

def test_domain_classification_schema_valid():
    dc = DomainClassification(
        domains=["macos", "vault"],
        confidence="high",
        reasoning="Prompt contains 'vault' and 'macos' keywords.",
    )
    assert dc.domains == ["macos", "vault"]
    assert dc.confidence == "high"


def test_domain_classification_confidence_validation():
    with pytest.raises(Exception):
        DomainClassification(
            domains=[],
            confidence="VERY_HIGH",  # invalid — not in pattern
            reasoning="test",
        )


# ---------------------------------------------------------------------------
# RunnableLambda wrapper (Component 5 integration)
# ---------------------------------------------------------------------------

def test_make_classifier_runnable_returns_domains():
    runnable = make_classifier_runnable(use_llm=False)
    result = runnable.invoke({
        "prompt": "check nifty portfolio",
        "cwd": "/Users/x/workspace/claude_for_mac_local",
    })
    assert "domains" in result
    assert "market-intel" in result["domains"]
    assert "macos" in result["domains"]


def test_make_classifier_runnable_passes_through_inputs():
    runnable = make_classifier_runnable(use_llm=False)
    result = runnable.invoke({
        "prompt": "test",
        "cwd": "",
        "extra_key": "preserved",
    })
    assert result["extra_key"] == "preserved"
    assert "domains" in result




