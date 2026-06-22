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

_SUMMARIES_DDL = """
    CREATE TABLE session_summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        summary TEXT,
        tags TEXT DEFAULT '',
        turn_at INTEGER DEFAULT 0,
        created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )
"""


def _make_db(summaries: list[dict] | None = None) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    conn = sqlite3.connect(tmp.name)
    conn.execute(_SUMMARIES_DDL)
    for sm in (summaries or []):
        conn.execute(
            "INSERT INTO session_summaries (session_id, summary, tags, turn_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                sm["session_id"],
                sm.get("summary", ""),
                sm.get("tags", ""),
                sm.get("turn_at", 0),
                sm.get("created_at", "2026-01-01 00:00:00"),
            ),
        )
    conn.commit()
    conn.close()
    return Path(tmp.name)


@pytest.fixture
def db_path():
    return _make_db(summaries=[
        {"session_id": "aaa", "summary": "Discussed MCP server setup and FastMCP.", "tags": "mcp,fastmcp,server", "turn_at": 3, "created_at": "2026-06-02 09:00:00"},
        {"session_id": "aaa", "summary": "Added session__list_ids tool to reduce payload.", "tags": "session,tools,sqlite", "turn_at": 5, "created_at": "2026-06-02 10:00:00"},
        {"session_id": "bbb", "summary": "Reviewed gate logic.", "tags": "gates,security", "turn_at": 12, "created_at": "2026-06-01 08:00:00"},
    ])


# ---------------------------------------------------------------------------
# handle_list_ids
# ---------------------------------------------------------------------------

class TestHandleListIds:
    def test_returns_list_of_strings(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_list_ids()
        assert isinstance(result, list)
        assert all(isinstance(r, str) for r in result)

    def test_ordered_by_last_seen_desc(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_list_ids()
        assert result[0] == "aaa"
        assert result[1] == "bbb"

    def test_distinct_session_ids(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_list_ids()
        assert len(result) == 2

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
        # result is plain strings, no dicts with blob fields
        for item in result:
            assert not isinstance(item, dict)


# ---------------------------------------------------------------------------
# handle_list / handle_list_all
# ---------------------------------------------------------------------------

class TestHandleList:
    def test_returns_distinct_sessions(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_list()
        assert len(result) == 2

    def test_fields_present(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_list()
        assert "session_id" in result[0]
        assert "last_seen" in result[0]

    def test_ordered_by_last_seen_desc(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_list()
        assert result[0]["session_id"] == "aaa"

    def test_list_all_delegates_to_list(self, db_path):
        with patch("tools.session._DB", db_path):
            assert handle_list() == handle_list_all()


# ---------------------------------------------------------------------------
# handle_get
# ---------------------------------------------------------------------------

class TestHandleGet:
    def test_returns_summaries_for_session(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_get("aaa")
        assert result["session_id"] == "aaa"
        assert len(result["summaries"]) == 2

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
            assert result["ok"] is True
            assert result["deleted"] == "aaa"
            assert result["rows"] == 2
            assert handle_get("aaa").get("error") is not None

    def test_delete_nonexistent_is_ok(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_delete("zzz")
        assert result["ok"] is True
        assert result["rows"] == 0


# ---------------------------------------------------------------------------
# handle_save_summary / handle_get_summaries / handle_delete_summary
# ---------------------------------------------------------------------------

class TestSummaries:
    def test_save_and_retrieve(self, db_path):
        with patch("tools.session._DB", db_path):
            save_result = handle_save_summary("bbb", "Test summary.", ["tag1", "tag2"], turn_at=10)
            assert save_result["ok"] is True
            summaries = handle_get_summaries("bbb")
        assert len(summaries) == 2  # existing + new
        last = summaries[-1]
        assert last["summary"] == "Test summary."
        assert last["tags"] == ["tag1", "tag2"]
        assert last["turn_at"] == 10

    def test_multiple_summaries_ordered(self, db_path):
        with patch("tools.session._DB", db_path):
            summaries = handle_get_summaries("aaa")
        assert len(summaries) == 2
        assert summaries[0]["turn_at"] == 3
        assert summaries[1]["turn_at"] == 5

    def test_get_summaries_empty(self, db_path):
        with patch("tools.session._DB", db_path):
            result = handle_get_summaries("zzz")
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
