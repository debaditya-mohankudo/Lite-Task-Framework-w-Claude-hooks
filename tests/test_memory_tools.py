"""Tests for src/tools/memory.py — MCP memory tool handlers."""
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.memory import (
    handle_add,
    handle_get,
    handle_list,
    handle_list_domains,
    handle_search,
    handle_delete,
    handle_tool_hints,
    handle_read_compact,
)

_MEMORY_DDL = """
    CREATE TABLE memories (
        id       INTEGER PRIMARY KEY AUTOINCREMENT,
        name     TEXT UNIQUE NOT NULL,
        type     TEXT NOT NULL,
        domain   TEXT DEFAULT 'global',
        priority INTEGER DEFAULT 50,
        tags     TEXT DEFAULT '',
        body     TEXT DEFAULT '',
        updated  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""

_TOOL_HINTS_DDL = """
    CREATE TABLE mcp_tool_hints (
        tool_name      TEXT PRIMARY KEY,
        domain         TEXT,
        count          INTEGER DEFAULT 0,
        last_used      TEXT,
        avg_latency_ms REAL,
        keywords       TEXT,
        skill          TEXT
    )
"""


def _make_memory_db(memories: list[dict] | None = None) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    con = sqlite3.connect(tmp.name)
    con.execute(_MEMORY_DDL)
    for m in (memories or []):
        con.execute(
            "INSERT INTO memories (name, type, domain, priority, tags, body) VALUES (:name, :type, :domain, :priority, :tags, :body)",
            m,
        )
    con.commit()
    con.close()
    return Path(tmp.name)


def _make_tool_hints_db(hints: list[dict] | None = None) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    con = sqlite3.connect(tmp.name)
    con.execute(_TOOL_HINTS_DDL)
    for h in (hints or []):
        con.execute(
            "INSERT INTO mcp_tool_hints (tool_name, domain, count, last_used, avg_latency_ms, keywords, skill) VALUES (:tool_name, :domain, :count, :last_used, :avg_latency_ms, :keywords, :skill)",
            h,
        )
    con.commit()
    con.close()
    return Path(tmp.name)


@pytest.fixture
def mem_db():
    return _make_memory_db([
        {"name": "alpha", "type": "user", "domain": "global", "priority": 10, "tags": "foo,bar", "body": "User is a developer"},
        {"name": "beta", "type": "feedback", "domain": "macos", "priority": 20, "tags": "macos", "body": "Use short responses"},
        {"name": "gamma", "type": "project", "domain": "market-intel", "priority": 50, "tags": "market", "body": "Market project context"},
    ])


@pytest.fixture
def hints_db():
    return _make_tool_hints_db([
        {"tool_name": "imessage__send", "domain": "macos", "count": 10, "last_used": "2026-06-01", "avg_latency_ms": 120.0, "keywords": "message send", "skill": "local-mac-imessage"},
        {"tool_name": "calendar__list", "domain": "macos", "count": 5, "last_used": "2026-06-02", "avg_latency_ms": 80.0, "keywords": "calendar", "skill": "local-mac-calendar"},
        {"tool_name": "market__prices", "domain": "market-intel", "count": 20, "last_used": "2026-06-03", "avg_latency_ms": 200.0, "keywords": "prices market", "skill": "market-intel-live-prices"},
    ])


# ---------------------------------------------------------------------------
# handle_add
# ---------------------------------------------------------------------------

def test_add_inserts_new_memory(mem_db):
    with patch("tools.memory.MEMORY_DB", str(mem_db)):
        result = handle_add(name="new-mem", type="user", body="New memory body")
    assert result["ok"] is True
    assert result["name"] == "new-mem"
    assert result["action"] == "upserted"


def test_add_updates_existing_memory(mem_db):
    with patch("tools.memory.MEMORY_DB", str(mem_db)):
        handle_add(name="alpha", type="user", body="Updated body")
        result = handle_get.__wrapped__(name="alpha") if hasattr(handle_get, "__wrapped__") else None
    con = sqlite3.connect(str(mem_db))
    row = con.execute("SELECT body FROM memories WHERE name='alpha'").fetchone()
    con.close()
    assert row[0] == "Updated body"


def test_add_rejects_invalid_type(mem_db):
    with patch("tools.memory.MEMORY_DB", str(mem_db)):
        result = handle_add(name="bad", type="invalid_type", body="body")
    assert "error" in result
    assert "invalid_type" in result["error"]


def test_add_default_domain_is_global(mem_db):
    with patch("tools.memory.MEMORY_DB", str(mem_db)):
        handle_add(name="new-global", type="user", body="body")
    con = sqlite3.connect(str(mem_db))
    row = con.execute("SELECT domain FROM memories WHERE name='new-global'").fetchone()
    con.close()
    assert row[0] == "global"


# ---------------------------------------------------------------------------
# handle_get
# ---------------------------------------------------------------------------

def test_get_returns_existing_memory(mem_db):
    with patch("tools.memory.MEMORY_DB", str(mem_db)):
        result = handle_get(name="alpha")
    assert result["name"] == "alpha"
    assert result["body"] == "User is a developer"


def test_get_returns_error_for_missing_name(mem_db):
    with patch("tools.memory.MEMORY_DB", str(mem_db)):
        result = handle_get(name="nonexistent")
    assert "error" in result


# ---------------------------------------------------------------------------
# handle_list
# ---------------------------------------------------------------------------

def test_list_returns_all_memories(mem_db):
    with patch("tools.memory.MEMORY_DB", str(mem_db)):
        result = handle_list()
    assert result["count"] == 3


def test_list_filters_by_type(mem_db):
    with patch("tools.memory.MEMORY_DB", str(mem_db)):
        result = handle_list(type="feedback")
    assert result["count"] == 1
    assert result["memories"][0]["name"] == "beta"


def test_list_filters_by_domain(mem_db):
    with patch("tools.memory.MEMORY_DB", str(mem_db)):
        result = handle_list(domain="macos")
    assert result["count"] == 1
    assert result["memories"][0]["name"] == "beta"


def test_list_returns_empty_for_no_match(mem_db):
    with patch("tools.memory.MEMORY_DB", str(mem_db)):
        result = handle_list(domain="nonexistent")
    assert result["count"] == 0


# ---------------------------------------------------------------------------
# handle_search
# ---------------------------------------------------------------------------

def test_search_matches_body(mem_db):
    with patch("tools.memory.MEMORY_DB", str(mem_db)):
        result = handle_search(query="developer")
    assert result["count"] >= 1
    names = [r["name"] for r in result["results"]]
    assert "alpha" in names


def test_search_matches_tags(mem_db):
    with patch("tools.memory.MEMORY_DB", str(mem_db)):
        result = handle_search(query="macos")
    assert result["count"] >= 1


def test_search_no_results_returns_empty(mem_db):
    with patch("tools.memory.MEMORY_DB", str(mem_db)):
        result = handle_search(query="zzznomatchzzz")
    assert result["count"] == 0


def test_search_multi_word_fallback(mem_db):
    with patch("tools.memory.MEMORY_DB", str(mem_db)):
        result = handle_search(query="developer market")
    assert result["count"] >= 1


def test_search_filters_by_domain(mem_db):
    with patch("tools.memory.MEMORY_DB", str(mem_db)):
        result = handle_search(query="context", domain="market-intel")
    assert all(r["domain"] == "market-intel" for r in result["results"])


# ---------------------------------------------------------------------------
# handle_list_domains
# ---------------------------------------------------------------------------

def test_list_domains_returns_memories_from_multiple_domains(mem_db):
    with patch("tools.memory.MEMORY_DB", str(mem_db)):
        result = handle_list_domains(domains="global,macos")
    assert result["count"] == 2
    domains = {r["domain"] for r in result["memories"]}
    assert domains == {"global", "macos"}


def test_list_domains_empty_string_returns_error(mem_db):
    with patch("tools.memory.MEMORY_DB", str(mem_db)):
        result = handle_list_domains(domains="")
    assert "error" in result


def test_list_domains_filters_by_type(mem_db):
    with patch("tools.memory.MEMORY_DB", str(mem_db)):
        result = handle_list_domains(domains="global,macos", type="user")
    assert result["count"] == 1
    assert result["memories"][0]["name"] == "alpha"


# ---------------------------------------------------------------------------
# handle_delete
# ---------------------------------------------------------------------------

def test_delete_removes_existing_memory(mem_db):
    with patch("tools.memory.MEMORY_DB", str(mem_db)):
        result = handle_delete(name="alpha")
    assert result["ok"] is True
    assert result["deleted"] == "alpha"
    con = sqlite3.connect(str(mem_db))
    row = con.execute("SELECT 1 FROM memories WHERE name='alpha'").fetchone()
    con.close()
    assert row is None


def test_delete_returns_error_for_missing_name(mem_db):
    with patch("tools.memory.MEMORY_DB", str(mem_db)):
        result = handle_delete(name="nonexistent")
    assert "error" in result


# ---------------------------------------------------------------------------
# handle_tool_hints
# ---------------------------------------------------------------------------

def test_tool_hints_returns_all(hints_db):
    with patch("tools.memory.TOOL_HINTS_DB", hints_db):
        result = handle_tool_hints()
    assert result["count"] == 3


def test_tool_hints_filters_by_domain(hints_db):
    with patch("tools.memory.TOOL_HINTS_DB", hints_db):
        result = handle_tool_hints(domain="macos")
    assert result["count"] == 2
    assert all(t["domain"] == "macos" for t in result["tools"])


def test_tool_hints_sorted_by_count_desc(hints_db):
    with patch("tools.memory.TOOL_HINTS_DB", hints_db):
        result = handle_tool_hints()
    counts = [t["count"] for t in result["tools"]]
    assert counts == sorted(counts, reverse=True)


def test_tool_hints_missing_db_returns_error(tmp_path):
    with patch("tools.memory.TOOL_HINTS_DB", tmp_path / "nonexistent.sqlite"):
        result = handle_tool_hints()
    assert "error" in result


def test_tool_hints_top_n_respected(hints_db):
    with patch("tools.memory.TOOL_HINTS_DB", hints_db):
        result = handle_tool_hints(top_n=1)
    assert result["count"] == 1


# ---------------------------------------------------------------------------
# handle_read_compact
# ---------------------------------------------------------------------------

def test_read_compact_returns_summary(tmp_path):
    session_id = "test-session-123"
    project_dir = tmp_path / ".claude" / "projects" / "my-project"
    project_dir.mkdir(parents=True)
    jsonl = project_dir / f"{session_id}.jsonl"
    summary_content = (
        "This session is being continued from a previous conversation that was "
        "too long.\n\nSummary:\nWe discussed turbovec upgrade.\n\nIf you need specific details, ask."
    )
    jsonl.write_text(
        json.dumps({"type": "user", "message": {"content": summary_content}}) + "\n"
    )
    with patch("tools.memory.Path.home", return_value=tmp_path):
        result = handle_read_compact(session_id=session_id)
    assert result["session_id"] == session_id
    assert "turbovec" in result["summary"]


def test_read_compact_no_jsonl_returns_error(tmp_path):
    (tmp_path / ".claude" / "projects").mkdir(parents=True)
    with patch("tools.memory.Path.home", return_value=tmp_path):
        result = handle_read_compact(session_id="nonexistent-session")
    assert "error" in result


def test_read_compact_no_compact_marker_returns_error(tmp_path):
    session_id = "test-no-compact"
    project_dir = tmp_path / ".claude" / "projects" / "proj"
    project_dir.mkdir(parents=True)
    jsonl = project_dir / f"{session_id}.jsonl"
    jsonl.write_text(json.dumps({"type": "user", "message": {"content": "regular message"}}) + "\n")
    with patch("tools.memory.Path.home", return_value=tmp_path):
        result = handle_read_compact(session_id=session_id)
    assert "error" in result
