"""Tests for task-framework: task_graph push/pop, load_task_history session scope."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tasks_db(path: Path, task_id: str = "task-abc") -> None:
    """Create a minimal proj_tasks.db with one task and some events."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE open_tasks (
            id TEXT PRIMARY KEY, title TEXT, body TEXT,
            status TEXT DEFAULT 'open', tags TEXT,
            issue_type TEXT DEFAULT 'task',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT, prompt_id TEXT, session_id TEXT,
            turn INTEGER, summary TEXT, tools TEXT, related TEXT DEFAULT ''
        );
    """)
    conn.execute(
        "INSERT INTO open_tasks (id, title, status) VALUES (?, ?, 'open')",
        (task_id, "Test task"),
    )
    conn.commit()
    conn.close()


def _insert_events(db: Path, task_id: str, session_id: str, count: int, base_turn: int = 0) -> None:
    conn = sqlite3.connect(str(db))
    for i in range(count):
        conn.execute(
            "INSERT INTO task_events (task_id, session_id, turn, summary, tools) VALUES (?,?,?,?,?)",
            (task_id, session_id, base_turn + i, f"turn {base_turn + i}", "Bash"),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# load_task_history — hybrid scope
# ---------------------------------------------------------------------------

class TestLoadTaskHistoryHybridScope:
    """Verify session-only scope logic."""

    def _call(self, db_path: Path, task_id: str, session_id: str) -> dict:
        from langchain_learning.nodes.load_task_history import LoadTaskHistoryNode
        node = LoadTaskHistoryNode()
        mock_cfg = MagicMock()
        mock_cfg.tasks_db = db_path
        state = {"active_task_id": task_id, "session_id": session_id}
        with patch("langchain_learning.nodes.load_task_history._cfg", mock_cfg):
            return node(state)

    def test_no_active_task_returns_empty(self, tmp_path):
        db = tmp_path / "tasks.db"
        _make_tasks_db(db)
        result = self._call(db, "", "sess-1")
        assert result == {"task_context": []}

    def test_cross_session_returns_all_turns(self, tmp_path):
        """Events from prior sessions are included — no session_id filter."""
        db = tmp_path / "tasks.db"
        _make_tasks_db(db, "t1")
        _insert_events(db, "t1", "old-sess", 3, base_turn=0)
        _insert_events(db, "t1", "new-sess", 2, base_turn=10)

        result = self._call(db, "t1", "new-sess")
        ctx = result["task_context"]
        assert len(ctx) == 5
        assert ctx[0]["session_id"] == "old-sess"
        assert ctx[3]["session_id"] == "new-sess"

    def test_limit_caps_at_20(self, tmp_path):
        """More than 20 events → only last 20 returned."""
        db = tmp_path / "tasks.db"
        _make_tasks_db(db, "t1")
        _insert_events(db, "t1", "old-sess", 10, base_turn=0)
        _insert_events(db, "t1", "cur-sess", 15, base_turn=100)

        result = self._call(db, "t1", "cur-sess")
        ctx = result["task_context"]
        assert len(ctx) == 20

    def test_within_limit_returns_all(self, tmp_path):
        """3 old + 13 new = 16 events — all returned (under limit)."""
        db = tmp_path / "tasks.db"
        _make_tasks_db(db, "t1")
        _insert_events(db, "t1", "old-sess", 3, base_turn=0)
        _insert_events(db, "t1", "cur-sess", 13, base_turn=50)

        result = self._call(db, "t1", "cur-sess")
        ctx = result["task_context"]
        assert len(ctx) == 16

    def test_db_missing_returns_empty(self, tmp_path):
        db = tmp_path / "nonexistent.db"
        result = self._call(db, "t1", "sess-1")
        assert result == {"task_context": []}

    def test_events_ordered_oldest_first(self, tmp_path):
        """Session events must be ordered oldest-first."""
        db = tmp_path / "tasks.db"
        _make_tasks_db(db, "t1")
        _insert_events(db, "t1", "cur-sess", 3, base_turn=0)

        result = self._call(db, "t1", "cur-sess")
        ctx = result["task_context"]
        turns = [r["turn"] for r in ctx]
        assert turns == sorted(turns)
