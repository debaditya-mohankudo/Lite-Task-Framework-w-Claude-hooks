"""Tests for src/tools/session.py — direct SQLite session tool handlers."""
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.session import (
    handle_list,
    handle_list_all,
    handle_list_ids,
    handle_get,
    handle_delete,
    handle_save_summary,
    handle_get_summaries,
    handle_delete_summary,
    handle_search,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION_DDL = """
    CREATE TABLE sessions (
        session_id TEXT PRIMARY KEY,
        turn       INTEGER DEFAULT 0,
        prompt_id  TEXT DEFAULT '',
        updated_at TEXT
    )
"""

_SUMMARIES_DDL = """
    CREATE TABLE session_summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        summary TEXT,
        tags TEXT,
        turn_at INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
"""


def _make_db(sessions: list[dict] | None = None, summaries: list[dict] | None = None) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    conn = sqlite3.connect(tmp.name)
    conn.execute(_SESSION_DDL)
    conn.execute(_SUMMARIES_DDL)
    for s in (sessions or []):
        conn.execute(
            """INSERT INTO sessions (session_id, turn, prompt_id, updated_at)
               VALUES (:session_id, :turn, :prompt_id, :updated_at)""",
            {
                "session_id": s.get("session_id", "test-id"),
                "turn":       s.get("turn", 1),
                "prompt_id":  s.get("prompt_id", ""),
                "updated_at": s.get("updated_at", "2026-01-01 00:00:00"),
            },
        )
    for sm in (summaries or []):
        conn.execute(
            "INSERT INTO session_summaries (session_id, summary, tags, turn_at) VALUES (?, ?, ?, ?)",
            (sm["session_id"], sm.get("summary", ""), sm.get("tags", ""), sm.get("turn_at", 0)),
        )
    conn.commit()
    conn.close()
    return Path(tmp.name)


@pytest.fixture
def db_path():
    return _make_db(
        sessions=[
            {"session_id": "aaa", "turn": 5,  "updated_at": "2026-06-02 10:00:00"},
            {"session_id": "bbb", "turn": 12, "updated_at": "2026-06-01 08:00:00"},
        ],
        summaries=[
            {"session_id": "aaa", "summary": "Discussed MCP server setup and FastMCP.", "tags": "mcp,fastmcp,server", "turn_at": 3},
            {"session_id": "aaa", "summary": "Added session__list_ids tool to reduce payload.", "tags": "session,tools,sqlite", "turn_at": 5},
        ],
    )


# ---------------------------------------------------------------------------
# handle_list_ids
# ---------------------------------------------------------------------------

class TestHandleListIds:
    def test_returns_minimal_fields_only(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_list_ids()
        assert len(result) == 2
        for row in result:
            assert set(row.keys()) == {"session_id", "turn", "updated_at"}

    def test_ordered_by_updated_at_desc(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_list_ids()
        assert result[0]["session_id"] == "aaa"
        assert result[1]["session_id"] == "bbb"

    def test_correct_values(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_list_ids()
        assert result[0]["turn"] == 5
        assert result[1]["turn"] == 12

    def test_empty_db(self):
        empty = _make_db()
        with patch("tools.session._DB", empty):
            result = handle_list_ids()
        assert result == []

    def test_db_not_found(self, tmp_path):
        with patch("tools.session._DB", tmp_path / "no.db"):
            result = handle_list_ids()
        assert result == []

    def test_no_blob_fields_present(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_list_ids()
        for row in result:
            assert "keywords" not in row
            assert "domains" not in row
            assert "state_history" not in row
            assert "tasks" not in row


# ---------------------------------------------------------------------------
# handle_list / handle_list_all
# ---------------------------------------------------------------------------

class TestHandleList:
    def test_returns_all_sessions(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_list()
        assert len(result) == 2

    def test_full_fields_present(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_list()
        assert "session_id" in result[0]
        assert "turn" in result[0]
        assert "prompt_id" in result[0]
        assert "updated_at" in result[0]

    def test_list_all_delegates_to_list(self, db_path):
        with patch("tools.session._DB", db_path):
            assert handle_list() == handle_list_all()


# ---------------------------------------------------------------------------
# handle_get
# ---------------------------------------------------------------------------

class TestHandleGet:
    def test_returns_session(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_get("aaa")
        assert result["session_id"] == "aaa"
        assert result["turn"] == 5

    def test_unknown_id_returns_error(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_get("zzz")
        assert "error" in result

    def test_db_not_found(self, tmp_path):
        with patch("tools.session._DB", tmp_path / "no.db"):
            result = handle_get("aaa")
        assert "error" in result


# ---------------------------------------------------------------------------
# handle_delete
# ---------------------------------------------------------------------------

class TestHandleDelete:
    def test_deletes_existing(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_delete("aaa")
            assert result == {"ok": True, "deleted": "aaa"}
            assert handle_get("aaa").get("error") is not None

    def test_delete_nonexistent_is_ok(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_delete("zzz")
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# handle_save_summary / handle_get_summaries / handle_delete_summary
# ---------------------------------------------------------------------------

class TestSummaries:
    def test_save_and_retrieve(self, db_path):
        with patch("tools.session._DB", db_path):
            save_result = handle_save_summary("bbb", "Test summary.", ["tag1", "tag2"], turn_at=10)
            assert save_result["ok"] is True
            summaries = handle_get_summaries("bbb")
        assert len(summaries) == 1
        assert summaries[0]["summary"] == "Test summary."
        assert summaries[0]["tags"] == ["tag1", "tag2"]
        assert summaries[0]["turn_at"] == 10

    def test_multiple_summaries_ordered(self, db_path):
        with patch("tools.session._DB", db_path):
            summaries = handle_get_summaries("aaa")
        assert len(summaries) == 2
        assert summaries[0]["turn_at"] == 3
        assert summaries[1]["turn_at"] == 5

    def test_get_summaries_empty(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_get_summaries("bbb")
        assert result == []

    def test_delete_summary(self, db_path):
        with patch("tools.session._DB", db_path):
            summaries = handle_get_summaries("aaa")
            sid = summaries[0]["id"]
            result = handle_delete_summary("aaa", sid)
            assert result["ok"] is True
            remaining = handle_get_summaries("aaa")
        assert len(remaining) == 1

    def test_save_no_tags(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_save_summary("bbb", "No tags summary.")
        assert result["ok"] is True


# ---------------------------------------------------------------------------
# handle_search
# ---------------------------------------------------------------------------

class TestHandleSearch:
    def test_finds_by_tag(self, db_path):
        with patch("tools.session._DB", db_path):
            results = handle_search("mcp fastmcp")
        assert len(results) >= 1
        assert results[0]["session_id"] == "aaa"

    def test_tag_weighted_higher_than_body(self, db_path):
        with patch("tools.session._DB", db_path):
            results = handle_search("session")
        assert results[0]["score"] >= 3

    def test_no_match_returns_empty(self, db_path):
        with patch("tools.session._DB", db_path):
            results = handle_search("xyzzy nonexistent")
        assert results == []

    def test_scoped_to_session_id(self, db_path):
        with patch("tools.session._DB", db_path):
            results = handle_search("mcp", session_id="bbb")
        assert results == []

    def test_top_k_respected(self, db_path):
        with patch("tools.session._DB", db_path):
            results = handle_search("mcp session", top_k=1)
        assert len(results) <= 1
