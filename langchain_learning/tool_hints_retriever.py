"""Component 4 — Tool Hints / Registry.

LangChain concept: EnsembleRetriever (hybrid retrieval)
Replaces: server/core/db/hints_db.py + server/core/tool_registry.py

Hybrid retrieval combines two signals:
  Signal A — BM25Retriever:   keyword overlap between prompt tokens and tool's
                               accumulated keyword vocabulary (learned from usage).
  Signal B — DomainRetriever: domain match between prompt's detected domains
                               and tool's domain tag.

LangChain EnsembleRetriever merges both ranked lists via Reciprocal Rank Fusion
(RRF), producing a single ranked list without manual weight tuning.

Why EnsembleRetriever over plain scoring?
  - BM25 alone misses tools whose keywords don't overlap with prompt wording.
  - Domain-only match is too coarse (all macos tools score equally).
  - RRF combination promotes tools that score well on BOTH signals.
  - Swappable: replace DomainRetriever with a vector retriever later (Component 4b).
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import List, Optional

from src.logger import get_logger
_log = get_logger(__name__)

from langchain_community.retrievers import BM25Retriever
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from langchain_core.documents import Document
from langchain_core.retrievers import BaseRetriever
from langchain_classic.retrievers import EnsembleRetriever
from pydantic import Field

from langchain_learning.config import config as _cfg


# ---------------------------------------------------------------------------
# Helpers — load tool documents from tool_hints.sqlite
# ---------------------------------------------------------------------------

def _load_tool_documents(db_path: str | Path, domain_filter: Optional[str] = None) -> list[Document]:
    """Read mcp_tool_hints rows and convert to LangChain Documents.

    page_content = tool_name + keywords (what BM25 indexes)
    metadata     = structured fields for downstream filtering
    """
    path = Path(db_path)
    if not path.exists():
        _log.warning("tool_hints.sqlite not found at %s", path)
        return []

    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # include recent_prompts if the column exists (added by prompt storage feature)
        cols = {r[1] for r in conn.execute("PRAGMA table_info(mcp_tool_hints)").fetchall()}
        select = "tool_name, domain, skill, count, last_used, avg_latency_ms, keywords"
        if "recent_prompts" in cols:
            select += ", recent_prompts"
        query = f"SELECT {select} FROM mcp_tool_hints"
        params: list = []
        if domain_filter:
            query += " WHERE domain = ?"
            params.append(domain_filter)
        query += " ORDER BY count DESC LIMIT 50"  # ~50 tools in DB; cap prevents BM25 corpus bloat if table grows
        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    docs = []
    for row in rows:
        keywords = row["keywords"] or ""

        # Build richer BM25 corpus: tool name tokens + keywords + recent prompt text.
        # recent_prompts is a JSON array of raw prompts the tool was called from.
        # This lets BM25 match "drop a line to john" against imessage__send even though
        # the keywords column only has "send,message,contact".
        recent_text = ""
        if "recent_prompts" in cols:
            try:
                prompts: list[str] = json.loads(row["recent_prompts"] or "[]")
                recent_text = " ".join(prompts)
            except Exception:
                pass

        content = f"{row['tool_name'].replace('__', ' ')} {keywords.replace(',', ' ')} {recent_text}".strip()

        docs.append(Document(
            page_content=content,
            metadata={
                "tool_name":      row["tool_name"],
                "domain":         row["domain"] or "global",
                "skill":          row["skill"] or "",
                "count":          row["count"] or 0,
                "last_used":      row["last_used"] or "",
                "avg_latency_ms": row["avg_latency_ms"] or 0,
                "keywords":       keywords,
            },
        ))
    return docs


# ---------------------------------------------------------------------------
# Signal B — DomainRetriever
# Returns tools whose domain matches any of the detected domains.
# Ranked by usage count (most-used tools in matching domain first).
# ---------------------------------------------------------------------------

class DomainToolRetriever(BaseRetriever):
    """Returns tools matching detected domains, ranked by usage count.

    LangChain concept taught here: custom BaseRetriever as a retrieval signal.
    This acts as the 'semantic' side of the ensemble — domain is a coarse
    but reliable signal that BM25 alone cannot capture.
    """

    db_path: str = Field(default_factory=lambda: str(_cfg.tool_hints_db))
    domains: List[str] = Field(default_factory=list)
    k: int = Field(default=10)

    def _get_relevant_documents(
        self, query: str, *, run_manager: CallbackManagerForRetrieverRun
    ) -> List[Document]:
        docs = _load_tool_documents(self.db_path)
        if not self.domains:
            return sorted(docs, key=lambda d: d.metadata["count"], reverse=True)

        matched = [d for d in docs if d.metadata["domain"] in self.domains]
        matched.sort(key=lambda d: d.metadata["count"], reverse=True)
        return matched[: self.k]


# ---------------------------------------------------------------------------
# Domain diversity cap
# ---------------------------------------------------------------------------

def _apply_domain_cap(
    ranked: list[Document],
    max_per_domain: int,
    top_k: int,
) -> list[Document]:
    """Return up to top_k docs with at most max_per_domain per domain.

    Problem solved:
        When one domain (e.g. "macos") dominates tool_hints.sqlite by usage
        count, BM25 + domain fusion returns 8-9 macos tools in the top-10,
        crowding out tools from other relevant domains entirely. The injected
        system prompt then suggests only macos tools regardless of the prompt.

    This fix:
        Walk the ranked list in order; once a domain hits max_per_domain,
        skip further tools from it. Logs skipped domains so saturation is
        visible without silently affecting results.

    Better solutions (in order of sophistication):
        1. Per-domain BM25 score normalisation — score each tool relative to
           its domain peers before fusion, so a high-count domain doesn't
           automatically dominate.
        2. Saturation-triggered expansion (look-ahead) — only activate capping
           when ≥ N of the next-10 ranked tools share a domain; avoids
           penalising legitimate domain focus.
        3. MMR (Maximal Marginal Relevance) — standard diversity algorithm that
           trades off relevance vs. redundancy per document, not per domain.
        4. Learned reranker — a small model that scores (prompt, tool) pairs
           directly, making domain a feature rather than a hard gate.
    """
    from collections import Counter
    domain_counts: dict[str, int] = {}
    diverse: list[Document] = []
    skipped_domains: list[str] = []

    for doc in ranked:
        d = doc.metadata["domain"]
        if domain_counts.get(d, 0) < max_per_domain:
            diverse.append(doc)
            domain_counts[d] = domain_counts.get(d, 0) + 1
        else:
            skipped_domains.append(d)
        if len(diverse) >= top_k:
            break

    if skipped_domains:
        _log.info(
            "tool_hints domain cap triggered: skipped %s (cap=%d)",
            dict(Counter(skipped_domains)),
            max_per_domain,
        )

    return diverse


# ---------------------------------------------------------------------------
# Public — ToolHintsRetriever (EnsembleRetriever wrapper)
# ---------------------------------------------------------------------------

class ToolHintsRetriever:
    """Hybrid tool retriever combining BM25 keyword match + domain match.

    LangChain concept: EnsembleRetriever
      - Takes N retrievers, each returning a ranked list.
      - Merges via Reciprocal Rank Fusion (RRF): score = sum(1 / (rank + k))
      - No manual weight tuning — position in each list drives the fusion.

    Usage:
        retriever = ToolHintsRetriever(domains=["macos", "vault"])
        docs = retriever.get_relevant_documents("send a message to john")
        # returns Document list, metadata has tool_name, domain, skill, count
    """

    def __init__(
        self,
        domains: Optional[List[str]] = None,
        db_path: Optional[str] = None,
        top_k: int = 10,
    ):
        self._db_path = db_path or str(_cfg.tool_hints_db)
        self._domains = domains or []
        self._top_k = top_k
        self._ensemble: Optional[EnsembleRetriever] = None

    def _build(self, docs: list[Document]) -> EnsembleRetriever:
        """Build the ensemble from current tool documents.

        Called lazily so empty DB returns gracefully.
        BM25Retriever is rebuilt each call (stateless, no index to maintain).
        """
        bm25 = BM25Retriever.from_documents(docs, k=self._top_k)

        domain_retriever = DomainToolRetriever(
            db_path=self._db_path,
            domains=self._domains,
            k=self._top_k,
        )

        return EnsembleRetriever(
            retrievers=[bm25, domain_retriever],
            weights=[0.6, 0.4],  # BM25 slightly favoured — keyword match is more precise
        )

    _MAX_PER_DOMAIN = 3

    def get_relevant_documents(self, query: str) -> list[Document]:
        """Retrieve top-k tools relevant to query via hybrid BM25 + domain fusion."""
        docs = _load_tool_documents(self._db_path)
        if not docs:
            _log.warning("no tool documents loaded from %s", self._db_path)
            return []
        ranked = self._build(docs).invoke(query)
        results = _apply_domain_cap(ranked, self._MAX_PER_DOMAIN, self._top_k)
        _log.info(
            "tool_hints selected: query=%r domains=%s top=%s",
            query[:60],
            self._domains,
            [(d.metadata["tool_name"], d.metadata["count"]) for d in results[:5]],
        )
        return results

    def as_runnable_input(self, query: str, domains: Optional[List[str]] = None) -> dict:
        """Convenience: return structured dict for pipeline use (Component 5)."""
        if domains:
            self._domains = domains
        results = self.get_relevant_documents(query)
        return {
            "query": query,
            "tool_hints": [
                {
                    "tool_name": d.metadata["tool_name"],
                    "domain":    d.metadata["domain"],
                    "skill":     d.metadata["skill"],
                    "count":     d.metadata["count"],
                }
                for d in results
            ],
        }
