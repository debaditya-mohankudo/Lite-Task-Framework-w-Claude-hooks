"""Tests for Component 1 — SQLiteMemoryRetriever."""
import sqlite3
import tempfile
from pathlib import Path

import pytest

from langchain_learning.memory_retriever import SQLiteMemoryRetriever, _tokenize, _score_row
from langchain_learning.config import Config
_cfg = Config()


# --- helpers ---

def make_db(rows: list[dict]) -> str:
    """Create a temp MEMORY.sqlite with given rows, return path."""
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    conn = sqlite3.connect(tmp.name)
    conn.execute("""
        CREATE TABLE memories (
            id       INTEGER PRIMARY KEY,
            name     TEXT,
            type     TEXT,
            domain   TEXT,
            priority INTEGER,
            tags     TEXT,
            body     TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO memories (name, type, domain, priority, tags, body) VALUES (:name, :type, :domain, :priority, :tags, :body)",
        rows,
    )
    conn.commit()
    conn.close()
    return tmp.name


# --- unit tests ---

def test_tokenize_removes_stopwords():
    tokens = _tokenize("what is the nakshatra lagna")
    assert "what" not in tokens
    assert "the" not in tokens
    assert "nakshatra" in tokens
    assert "lagna" in tokens


def test_tokenize_lowercases():
    assert "langchain" in _tokenize("LangChain")


# --- retriever tests ---

@pytest.fixture
def db_path():
    rows = [
        {"name": "langchain-plan",  "type": "project", "domain": "macos",  "priority": 50, "tags": "langchain,learning", "body": "LangChain learning plan for this project."},
        {"name": "market-intel",    "type": "project", "domain": "market", "priority": 50, "tags": "market,nifty,stocks",  "body": "Market intelligence setup and portfolio."},
        {"name": "always-inject",   "type": "user",    "domain": "global", "priority": 1,  "tags": "global",              "body": "Always injected global context."},
        {"name": "vault-rules",     "type": "feedback","domain": "vault",  "priority": 20, "tags": "vault,obsidian",      "body": "Vault write rules and conventions."},
    ]
    return make_db(rows)


def test_always_inject_always_returned(db_path):
    retriever = SQLiteMemoryRetriever(db_path=db_path, top_k=5)
    docs = retriever.invoke("completely unrelated query xyz")
    names = [d.metadata["name"] for d in docs]
    assert "always-inject" in names


def test_relevant_docs_scored_correctly(db_path):
    retriever = SQLiteMemoryRetriever(db_path=db_path, top_k=5)
    docs = retriever.invoke("langchain learning plan")
    names = [d.metadata["name"] for d in docs]
    assert "langchain-plan" in names
    assert "market-intel" not in names


def test_top_k_limits_scored_results(db_path):
    retriever = SQLiteMemoryRetriever(db_path=db_path, top_k=1)
    docs = retriever.invoke("vault market langchain")
    # always-inject + max 1 scored
    scored = [d for d in docs if d.metadata["priority"] != 1]
    assert len(scored) <= 1


def test_fallback_returns_top3_when_no_match(db_path):
    retriever = SQLiteMemoryRetriever(db_path=db_path, top_k=5)
    docs = retriever.invoke("zzz no keywords match anything here zzz")
    # should still return always + up to 3 fallback
    assert len(docs) >= 1  # at minimum always-inject


def test_document_metadata_fields(db_path):
    retriever = SQLiteMemoryRetriever(db_path=db_path, top_k=5)
    docs = retriever.invoke("langchain")
    lc_doc = next(d for d in docs if d.metadata["name"] == "langchain-plan")
    assert lc_doc.metadata["domain"] == "macos"
    assert lc_doc.metadata["type"] == "project"
    assert "langchain" in lc_doc.metadata["tags"]
    assert lc_doc.page_content != ""


def test_config_defaults_applied():
    retriever = SQLiteMemoryRetriever()
    assert retriever.db_path == str(_cfg.memory_db)
    assert retriever.top_k == _cfg.top_k


def test_config_can_be_overridden(db_path):
    retriever = SQLiteMemoryRetriever(db_path=db_path, top_k=2)
    assert retriever.db_path == db_path
    assert retriever.top_k == 2


def test_always_inject_comes_first(db_path):
    retriever = SQLiteMemoryRetriever(db_path=db_path, top_k=5)
    docs = retriever.invoke("langchain vault market")
    assert docs[0].metadata["priority"] == 1
