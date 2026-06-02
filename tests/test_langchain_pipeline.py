"""Tests for Component 5 — LCEL Pipeline.

Strategy:
  - All IO uses temp DBs — no real MEMORY.sqlite or tool_hints.sqlite.
  - Pipeline built with use_llm=False (no API calls).
  - Tests verify: output shape, domain routing, retriever integration,
    parallel branch execution, and graceful degradation on missing DBs.
"""
import sqlite3
import tempfile
from pathlib import Path

import pytest

from langchain_learning.pipeline import (
    MemoryContext,
    build_memory_pipeline,
    run_pipeline,
    _docs_to_dicts,
    _make_memory_step,
    _make_tool_hints_step,
    _make_merge_step,
)
from langchain_core.documents import Document


# ---------------------------------------------------------------------------
# Fixtures — temp DBs
# ---------------------------------------------------------------------------

def _make_memory_db(rows: list[dict]) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    conn = sqlite3.connect(tmp.name)
    conn.execute("""
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY,
            name TEXT, type TEXT, domain TEXT,
            priority INTEGER DEFAULT 50,
            tags TEXT, body TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO memories (name,type,domain,priority,tags,body) VALUES (:name,:type,:domain,:priority,:tags,:body)",
        rows,
    )
    conn.commit()
    conn.close()
    return Path(tmp.name)


def _make_tool_hints_db(rows: list[dict]) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    conn = sqlite3.connect(tmp.name)
    conn.execute("""
        CREATE TABLE mcp_tool_hints (
            tool_name TEXT PRIMARY KEY,
            domain TEXT, skill TEXT,
            count INTEGER DEFAULT 0,
            last_used TEXT DEFAULT '',
            avg_latency_ms REAL DEFAULT 0.0,
            keywords TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO mcp_tool_hints (tool_name,domain,skill,count,keywords) VALUES (:tool_name,:domain,:skill,:count,:keywords)",
        rows,
    )
    conn.commit()
    conn.close()
    return Path(tmp.name)


@pytest.fixture
def memory_db():
    return _make_memory_db([
        {"name": "always-on", "type": "user", "domain": "global", "priority": 1,
         "tags": "global", "body": "always injected"},
        {"name": "astro-mem", "type": "project", "domain": "astrology", "priority": 20,
         "tags": "nakshatra rahu panchang", "body": "astrology data for nakshatra"},
        {"name": "market-mem", "type": "project", "domain": "market-intel", "priority": 20,
         "tags": "nifty gold fii", "body": "market data for nifty gold"},
        {"name": "vault-mem", "type": "reference", "domain": "vault", "priority": 20,
         "tags": "vault note write", "body": "vault operations"},
    ])


@pytest.fixture
def tool_hints_db():
    return _make_tool_hints_db([
        {"tool_name": "panchang__today",     "domain": "astrology",   "skill": "panchang", "count": 20, "keywords": "panchang,nakshatra,tithi"},
        {"tool_name": "market__gold_regime", "domain": "market-intel","skill": "gold",     "count": 15, "keywords": "gold,regime,market"},
        {"tool_name": "imessage__send",      "domain": "macos",       "skill": "imessage", "count": 50, "keywords": "send,message,contact"},
        {"tool_name": "vault__write",        "domain": "vault",       "skill": "vault",    "count": 40, "keywords": "write,save,note,vault"},
    ])


# ---------------------------------------------------------------------------
# _docs_to_dicts helper
# ---------------------------------------------------------------------------

def test_docs_to_dicts_extracts_metadata():
    docs = [Document(page_content="body text", metadata={"name": "x", "domain": "macos", "priority": 20})]
    result = _docs_to_dicts(docs)
    assert len(result) == 1
    assert result[0]["name"] == "x"
    assert result[0]["domain"] == "macos"
    assert result[0]["body"] == "body text"


def test_docs_to_dicts_body_not_overwritten_if_in_metadata():
    docs = [Document(page_content="page body", metadata={"body": "meta body", "name": "x"})]
    result = _docs_to_dicts(docs)
    # metadata body takes precedence (setdefault — only sets if absent)
    assert result[0]["body"] == "meta body"


def test_docs_to_dicts_empty():
    assert _docs_to_dicts([]) == []


# ---------------------------------------------------------------------------
# _make_memory_step
# ---------------------------------------------------------------------------

def test_memory_step_returns_memories_key(memory_db):
    step = _make_memory_step(db_path=memory_db)
    result = step.invoke({"prompt": "nakshatra today"})
    assert "memories" in result
    assert isinstance(result["memories"], list)


def test_memory_step_includes_always_inject(memory_db):
    step = _make_memory_step(db_path=memory_db)
    result = step.invoke({"prompt": "unrelated prompt xyz"})
    names = [m["name"] for m in result["memories"]]
    assert "always-on" in names


def test_memory_step_scores_relevant_memories(memory_db):
    step = _make_memory_step(db_path=memory_db)
    result = step.invoke({"prompt": "what is my nakshatra rahu"})
    names = [m["name"] for m in result["memories"]]
    assert "astro-mem" in names


def test_memory_step_missing_db_returns_empty():
    step = _make_memory_step(db_path=Path("/tmp/no_memory_pipeline.sqlite"))
    result = step.invoke({"prompt": "test"})
    assert result["memories"] == []


# ---------------------------------------------------------------------------
# _make_tool_hints_step
# ---------------------------------------------------------------------------

def test_tool_hints_step_returns_tool_hints_key(tool_hints_db):
    step = _make_tool_hints_step(db_path=tool_hints_db)
    result = step.invoke({"prompt": "nakshatra panchang", "domains": ["astrology"]})
    assert "tool_hints" in result
    assert isinstance(result["tool_hints"], list)


def test_tool_hints_step_domain_scoped(tool_hints_db):
    step = _make_tool_hints_step(db_path=tool_hints_db)
    result = step.invoke({"prompt": "send message to john", "domains": ["macos"]})
    tool_names = [h["tool_name"] for h in result["tool_hints"]]
    # imessage matches both domain (macos) and keyword (send/message)
    assert "imessage__send" in tool_names


def test_tool_hints_step_missing_db_returns_empty():
    step = _make_tool_hints_step(db_path=Path("/tmp/no_hints_pipeline.sqlite"))
    result = step.invoke({"prompt": "test", "domains": ["macos"]})
    assert result["tool_hints"] == []


# ---------------------------------------------------------------------------
# _make_merge_step
# ---------------------------------------------------------------------------

def test_merge_step_assembles_memory_context():
    step = _make_merge_step()
    inputs = {
        "prompt": "hello",
        "domains": ["macos"],
        "memories": {"memories": [{"name": "x", "domain": "macos"}]},
        "tool_hints": {"tool_hints": [{"tool_name": "imessage__send"}]},
    }
    result = step.invoke(inputs)
    assert result["prompt"] == "hello"
    assert result["domains"] == ["macos"]
    assert result["memories"][0]["name"] == "x"
    assert result["tool_hints"][0]["tool_name"] == "imessage__send"


def test_merge_step_handles_empty_branches():
    step = _make_merge_step()
    inputs = {
        "prompt": "p",
        "domains": [],
        "memories": {"memories": []},
        "tool_hints": {"tool_hints": []},
    }
    result = step.invoke(inputs)
    assert result["memories"] == []
    assert result["tool_hints"] == []


# ---------------------------------------------------------------------------
# Full pipeline — build_memory_pipeline
# ---------------------------------------------------------------------------

def test_pipeline_builds():
    pipeline = build_memory_pipeline(use_llm=False)
    assert pipeline is not None


def test_pipeline_output_shape(memory_db, tool_hints_db):
    pipeline = build_memory_pipeline(
        use_llm=False,
        memory_db=memory_db,
        tool_hints_db=tool_hints_db,
    )
    result = pipeline.invoke({"prompt": "nakshatra today", "cwd": ""})
    # MemoryContext keys
    assert "prompt" in result
    assert "domains" in result
    assert "memories" in result
    assert "tool_hints" in result


def test_pipeline_detects_astrology_domain(memory_db, tool_hints_db):
    pipeline = build_memory_pipeline(use_llm=False, memory_db=memory_db, tool_hints_db=tool_hints_db)
    result = pipeline.invoke({"prompt": "what is my nakshatra rahu panchang today", "cwd": ""})
    assert "astrology" in result["domains"]


def test_pipeline_retrieves_matching_memories(memory_db, tool_hints_db):
    pipeline = build_memory_pipeline(use_llm=False, memory_db=memory_db, tool_hints_db=tool_hints_db)
    result = pipeline.invoke({"prompt": "nakshatra rahu", "cwd": ""})
    names = [m["name"] for m in result["memories"]]
    assert "astro-mem" in names


def test_pipeline_always_inject_memory_present(memory_db, tool_hints_db):
    pipeline = build_memory_pipeline(use_llm=False, memory_db=memory_db, tool_hints_db=tool_hints_db)
    result = pipeline.invoke({"prompt": "completely unrelated prompt", "cwd": ""})
    names = [m["name"] for m in result["memories"]]
    assert "always-on" in names


def test_pipeline_retrieves_matching_tool_hints(memory_db, tool_hints_db):
    pipeline = build_memory_pipeline(use_llm=False, memory_db=memory_db, tool_hints_db=tool_hints_db)
    result = pipeline.invoke({"prompt": "panchang nakshatra today", "cwd": ""})
    tool_names = [h["tool_name"] for h in result["tool_hints"]]
    assert "panchang__today" in tool_names


def test_pipeline_cwd_domain_injected(memory_db, tool_hints_db):
    pipeline = build_memory_pipeline(use_llm=False, memory_db=memory_db, tool_hints_db=tool_hints_db)
    result = pipeline.invoke({"prompt": "something random", "cwd": "/Users/x/workspace/claude_for_mac_local"})
    assert "macos" in result["domains"]


def test_pipeline_missing_dbs_graceful():
    pipeline = build_memory_pipeline(
        use_llm=False,
        memory_db=Path("/tmp/no_mem.sqlite"),
        tool_hints_db=Path("/tmp/no_hints.sqlite"),
    )
    result = pipeline.invoke({"prompt": "nakshatra today", "cwd": ""})
    # domains still detected from keyword classifier (no DB needed)
    assert "astrology" in result["domains"]
    # but retrieval returns empty
    assert result["memories"] == []
    assert result["tool_hints"] == []


def test_pipeline_prompt_preserved_in_output(memory_db, tool_hints_db):
    pipeline = build_memory_pipeline(use_llm=False, memory_db=memory_db, tool_hints_db=tool_hints_db)
    result = pipeline.invoke({"prompt": "check nifty gold", "cwd": ""})
    assert result["prompt"] == "check nifty gold"


def test_pipeline_market_domain_and_tool(memory_db, tool_hints_db):
    pipeline = build_memory_pipeline(use_llm=False, memory_db=memory_db, tool_hints_db=tool_hints_db)
    result = pipeline.invoke({"prompt": "what is the gold market regime", "cwd": ""})
    assert "market-intel" in result["domains"]
    tool_names = [h["tool_name"] for h in result["tool_hints"]]
    assert "market__gold_regime" in tool_names


# ---------------------------------------------------------------------------
# run_pipeline convenience
# ---------------------------------------------------------------------------

def test_run_pipeline_returns_memory_context(memory_db, tool_hints_db):
    result = run_pipeline(
        "nakshatra today",
        memory_db=memory_db,
        tool_hints_db=tool_hints_db,
    )
    assert "prompt" in result
    assert "domains" in result
    assert "memories" in result
    assert "tool_hints" in result


def test_run_pipeline_uses_cwd(memory_db, tool_hints_db):
    result = run_pipeline(
        "write a note",
        cwd="/Users/x/workspace/K-mirror",
        memory_db=memory_db,
        tool_hints_db=tool_hints_db,
    )
    assert "philosophy" in result["domains"]
