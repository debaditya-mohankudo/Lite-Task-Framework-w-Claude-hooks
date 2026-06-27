"""Tests for issue_type column on open_tasks — create, update, validation."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.tools.tasks import handle_create, handle_get, handle_list, handle_update
from src.db.schema import OPEN_TASKS_DDL, TASK_EVENTS_DDL, TASK_EDGES_DDL


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    db = tmp_path / "proj_tasks.db"
    with patch("src.tools.tasks._DB", db):
        yield db


class TestCreateIssueType:
    def test_default_is_task(self):
        r = handle_create(title="My task", body="Type: feature\nTask: x\nResolution: y\nMotivation: z\nFiles: f")
        assert r["issue_type"] == "task"

    def test_explicit_epic(self):
        r = handle_create(title="Big epic", body="Type: feature\nTask: x\nResolution: y\nMotivation: z\nFiles: f", issue_type="epic")
        assert r["issue_type"] == "epic"

    def test_all_valid_types(self):
        for itype in ("epic", "story", "task", "bug", "subtask"):
            r = handle_create(title=f"t-{itype}", body="Type: feature\nTask: x\nResolution: y\nMotivation: z\nFiles: f", issue_type=itype)
            assert r["issue_type"] == itype

    def test_invalid_type_returns_error(self):
        r = handle_create(title="bad", body="Type: feature\nTask: x\nResolution: y\nMotivation: z\nFiles: f", issue_type="sprint")
        assert "error" in r


class TestUpdateIssueType:
    def _create(self, issue_type="task"):
        return handle_create(
            title="base task",
            body="Type: feature\nTask: x\nResolution: y\nMotivation: z\nFiles: f",
            issue_type=issue_type,
        )["id"]

    def test_update_issue_type(self):
        tid = self._create()
        r = handle_update(id=tid, issue_type="bug")
        assert r["issue_type"] == "bug"

    def test_update_preserves_issue_type_when_not_specified(self):
        tid = self._create(issue_type="story")
        r = handle_update(id=tid, title="new title")
        assert r["issue_type"] == "story"

    def test_update_invalid_type_returns_error(self):
        tid = self._create()
        r = handle_update(id=tid, issue_type="invalid")
        assert "error" in r


class TestGetAndListIssueType:
    def test_get_returns_issue_type(self):
        tid = handle_create(
            title="story task",
            body="Type: feature\nTask: x\nResolution: y\nMotivation: z\nFiles: f",
            issue_type="story",
        )["id"]
        r = handle_get(id=tid)
        assert r["issue_type"] == "story"

    def test_list_returns_issue_type(self):
        handle_create(
            title="listed epic",
            body="Type: feature\nTask: x\nResolution: y\nMotivation: z\nFiles: f",
            issue_type="epic",
        )
        rows = handle_list()
        assert any(t["issue_type"] == "epic" for t in rows)


BODY = "Type: feature\nTask: x\nResolution: y\nMotivation: z\nFiles: f"


class TestParentIdColumn:
    def _mk(self, title, parent_id="", issue_type="task"):
        return handle_create(title=title, body=BODY, parent_id=parent_id, issue_type=issue_type)["id"]

    def test_create_sets_parent_id_column(self):
        epic = self._mk("Epic", issue_type="epic")
        story = self._mk("Story", parent_id=epic, issue_type="story")
        row = handle_get(id=story)
        assert row["parent_id"] == epic

    def test_create_no_parent_has_null(self):
        tid = self._mk("Solo")
        row = handle_get(id=tid)
        assert row["parent_id"] is None

    def test_list_depth_zero_for_roots(self):
        self._mk("Root epic", issue_type="epic")
        rows = handle_list()
        roots = [r for r in rows if not r.get("parent_id")]
        assert all(r["depth"] == 0 for r in roots)

    def test_list_three_level_tree_order_and_depth(self):
        epic = self._mk("Epic", issue_type="epic")
        story = self._mk("Story", parent_id=epic, issue_type="story")
        subtask = self._mk("Subtask", parent_id=story, issue_type="subtask")
        rows = handle_list()
        ids = [r["id"] for r in rows]
        depths = {r["id"]: r["depth"] for r in rows}
        # DFS order: epic before story before subtask
        assert ids.index(epic) < ids.index(story) < ids.index(subtask)
        assert depths[epic] == 0
        assert depths[story] == 1
        assert depths[subtask] == 2

    def test_list_all_tasks_have_depth_field(self):
        self._mk("A")
        self._mk("B")
        rows = handle_list()
        assert all("depth" in r for r in rows)

    def test_migration_backfill_from_tags(self, tmp_path):
        """parent_id column is backfilled from parent:<id> tags on old DBs."""
        import sqlite3 as _sq
        import uuid
        from unittest.mock import patch

        db = tmp_path / "old.db"
        # Create a DB that looks like pre-parent_id schema (no parent_id column)
        conn = _sq.connect(str(db))
        conn.execute("""
            CREATE TABLE open_tasks (
                id TEXT PRIMARY KEY, title TEXT NOT NULL,
                body TEXT DEFAULT '', tags TEXT DEFAULT '',
                status TEXT DEFAULT 'open', issue_type TEXT DEFAULT 'task',
                created_at TIMESTAMP DEFAULT (datetime('now')),
                updated_at TIMESTAMP DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL, prompt_id TEXT DEFAULT '',
                session_id TEXT DEFAULT '', turn INTEGER DEFAULT 0,
                summary TEXT DEFAULT '', tools TEXT DEFAULT '',
                logged_at TIMESTAMP DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE task_edges (
                from_id TEXT NOT NULL, to_id TEXT NOT NULL,
                relation_type TEXT NOT NULL, created_at TIMESTAMP DEFAULT (datetime('now')),
                PRIMARY KEY (from_id, to_id, relation_type)
            )
        """)
        parent_id = uuid.uuid4().hex[:8]
        child_id = uuid.uuid4().hex[:8]
        conn.execute("INSERT INTO open_tasks (id, title, tags) VALUES (?, ?, ?)", (parent_id, "Parent", ""))
        conn.execute("INSERT INTO open_tasks (id, title, tags) VALUES (?, ?, ?)", (child_id, "Child", f"parent:{parent_id}"))
        conn.commit()
        conn.close()

        with patch("src.tools.tasks._DB", db):
            rows = handle_list()
        child_row = next(r for r in rows if r["id"] == child_id)
        assert child_row["parent_id"] == parent_id

    def test_cycle_guard_does_not_infinite_loop(self, tmp_path):
        """Cycle in parent_id (A→B→A) must not cause infinite recursion."""
        import sqlite3 as _sq
        import uuid
        from unittest.mock import patch

        db = tmp_path / "cycle.db"
        conn = _sq.connect(str(db))
        conn.executescript(OPEN_TASKS_DDL)
        conn.executescript(TASK_EVENTS_DDL)
        conn.executescript(TASK_EDGES_DDL)
        a, b = uuid.uuid4().hex[:8], uuid.uuid4().hex[:8]
        conn.execute("INSERT INTO open_tasks (id, title, parent_id) VALUES (?, ?, ?)", (a, "A", b))
        conn.execute("INSERT INTO open_tasks (id, title, parent_id) VALUES (?, ?, ?)", (b, "B", a))
        conn.commit()
        conn.close()

        with patch("src.tools.tasks._DB", db):
            rows = handle_list()
        assert len(rows) == 2  # both returned, no crash
