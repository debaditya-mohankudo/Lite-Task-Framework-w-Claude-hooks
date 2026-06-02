"""Component 1 — Memory Retrieval.

LangChain concept: BaseRetriever
Replaces: server/core/scorer.py (scoring logic) + hooks/memory_loader.py

Scoring weights mirror the existing MemoryScorer:
  tag match  = 3x
  name match = 2x
  body match = 1x

Priority boost: priority=1 rows always included (always-inject globals).
"""
import json
import re
import sqlite3
from typing import List

from src.logger import get_logger
_log = get_logger(__name__)

from langchain_core.retrievers import BaseRetriever
from langchain_core.documents import Document
from langchain_core.callbacks import CallbackManagerForRetrieverRun
from pydantic import Field

from langchain_learning.config import config as _cfg

_TAG_WEIGHT  = 3
_NAME_WEIGHT = 2
_BODY_WEIGHT = 1

_STOPWORDS_PATH = _cfg.stopwords_path


def _load_stopwords() -> set[str]:
    try:
        data = json.loads(_STOPWORDS_PATH.read_text())
    except Exception:
        return {"a", "an", "the", "is", "in", "on", "at", "to", "for", "of", "and", "or", "but"}
    else:
        words: set[str] = set()
        for bucket in data.values():
            words.update(w.lower() for w in bucket)
        return words


_STOPWORDS = _load_stopwords()


def _tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9_/-]+", text.lower())
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 1}


def _score_row(row: sqlite3.Row, keywords: set[str]) -> int:
    tag_words  = _tokenize(row["tags"] or "")
    name_words = _tokenize(row["name"] or "")
    body_words = _tokenize(row["body"] or "")

    score = 0
    for kw in keywords:
        if kw in tag_words:  score += _TAG_WEIGHT
        if kw in name_words: score += _NAME_WEIGHT
        if kw in body_words: score += _BODY_WEIGHT
    return score


def _row_to_document(row: sqlite3.Row, score: int) -> Document:
    return Document(
        page_content=row["body"] or "",
        metadata={
            "name":     row["name"],
            "type":     row["type"],
            "domain":   row["domain"],
            "priority": row["priority"],
            "tags":     row["tags"] or "",
            "score":    score,
        },
    )


class SQLiteMemoryRetriever(BaseRetriever):
    """Retrieves memories from MEMORY.sqlite scored against a query string.

    Stateless — opens and closes the DB connection per call.
    No LLM involved — pure keyword scoring.
    """

    db_path: str = Field(default_factory=lambda: str(_cfg.memory_db), description="Absolute path to MEMORY.sqlite")
    top_k: int   = Field(default_factory=lambda: _cfg.top_k, description="Max scored memories to return (always-inject rows are added on top)")

    def _get_relevant_documents(
        self,
        query: str,
        *,
        run_manager: CallbackManagerForRetrieverRun,
    ) -> List[Document]:
        keywords = _tokenize(query)
        _log.debug("retriever query=%r keywords=%s", query[:60], keywords)

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT name, type, domain, priority, tags, body FROM memories"
                ).fetchall()
        except Exception as exc:
            _log.error("failed to read MEMORY.sqlite at %s: %s", self.db_path, exc)
            return []

        always:  list[Document] = []
        scored:  list[tuple[int, Document]] = []

        for row in rows:
            if row["priority"] == 1:
                always.append(_row_to_document(row, score=999))
                continue

            s = _score_row(row, keywords)
            scored.append((s, _row_to_document(row, score=s)))

        scored.sort(key=lambda x: x[0], reverse=True)

        # include top_k scored rows with score > 0; fall back to top 3 if none match
        matched = [doc for s, doc in scored if s > 0][:self.top_k]
        if not matched:
            matched = [doc for _, doc in scored[:3]]

        _log.debug("always-inject=%d scored=%d returned=%d", len(always), len(matched), len(always) + len(matched))
        return always + matched
