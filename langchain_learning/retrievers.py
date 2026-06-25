"""Public Protocol contracts for the retrieval and gate layers.

These interfaces are the joints in the memory injection pipe:
  prompt → tokenise → MemoryRetriever → LoadMemoriesNode → additionalSystemPrompt

Concrete implementations live in nodes/ and hooks/ and import from here.
This file must never import from nodes/ or hooks/ — the dependency arrow is:
  nodes/ → retrievers.py, never the reverse.

Design principle: Callable classes with minimal structural contracts (ACME POC Principle 13).
Protocols are structural — no inheritance required to satisfy them.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

# GateContext is a dataclass defined in hooks/gates.py.
# We import the type only for the GatePolicy signature; the arrow goes:
#   hooks/gate_check.py → retrievers.py (for GatePolicy)
#   hooks/gates.py      → retrievers.py (only for GateContext type, which is fine)
# retrievers.py never calls into hooks/ — it only names the type.
from hooks.gates import GateContext


# ---------------------------------------------------------------------------
# MemoryRetriever
# ---------------------------------------------------------------------------

@runtime_checkable
class MemoryRetriever(Protocol):
    """Score and return relevant memories for a set of prompt tokens.

    Implementations:
      CombinationSignalRetriever — wraps score_memories() (nodes/_memory_scoring.py)
      NullMemoryRetriever        — returns [] (testing / disabled)
    """

    def retrieve(
        self,
        tokens: set[str],
        project_domain: str | None,
        top_n: int = 5,
    ) -> list[dict]: ...


# ---------------------------------------------------------------------------
# ToolScorer
# ---------------------------------------------------------------------------

@runtime_checkable
class ToolScorer(Protocol):
    """Score and return relevant tool hints for a set of prompt keywords.

    Implementations:
      KeywordOverlapScorer — wraps ScoreToolsNode inline scoring (nodes/score_tools.py)
      NullToolScorer       — returns [] (testing / disabled)
    """

    def score(
        self,
        keywords: set[str],
        domains: set[str],
        top_n: int = 5,
    ) -> list[dict]: ...


# ---------------------------------------------------------------------------
# GatePolicy
# ---------------------------------------------------------------------------

@runtime_checkable
class GatePolicy(Protocol):
    """Check whether a tool call should be allowed or denied.

    Implementations:
      DefaultGatePolicy — wraps GATES dict dispatch (hooks/gates.py)
    """

    def check(self, tool_name: str, ctx: GateContext) -> tuple[bool, str]: ...


# ---------------------------------------------------------------------------
# Null implementations — safe defaults for testing and opt-out
# ---------------------------------------------------------------------------

class NullMemoryRetriever:
    """No-op retriever — always returns empty. Satisfies MemoryRetriever Protocol."""

    def retrieve(self, tokens: set[str], project_domain: str | None, top_n: int = 5) -> list[dict]:
        return []


class NullToolScorer:
    """No-op scorer — always returns empty. Satisfies ToolScorer Protocol."""

    def score(self, keywords: set[str], domains: set[str], top_n: int = 5) -> list[dict]:
        return []
