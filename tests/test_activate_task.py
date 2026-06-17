"""Tests for ActivateTaskNode — PostToolUse bridge for task activation."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from langchain_learning.nodes.activate_task import ActivateTaskNode, _lookup_task, _score_memories


# ── helpers ──────────────────────────────────────────────────────────────────

def _make_tasks_db(tmp_path: Path) -> Path:
    db = tmp_path / "proj_tasks.db"
    with sqlite3.connect(str(db)) as conn:
        conn.execute("""
            CREATE TABLE open_tasks (
                id TEXT PRIMARY KEY,
                title TEXT,
                body TEXT,
                status TEXT DEFAULT 'open',
                tags TEXT DEFAULT '',
                updated_at TEXT,
                parent_id TEXT DEFAULT NULL
            )
        """)
        conn.execute("""
            CREATE VIEW IF NOT EXISTS open_tasks AS SELECT * FROM open_tasks
        """)
        conn.execute("INSERT INTO open_tasks VALUES ('task01', 'Fix auth bug', 'body text', 'open', '', datetime('now'), NULL)")
        conn.commit()
    return db


def _state(**kwargs) -> dict:
    base = {"session_id": "sess1234", "tool_name": "", "tool_input": {}, "task_stack": []}
    base.update(kwargs)
    return base


# ── no-op for unrelated tools ─────────────────────────────────────────────────

def test_noop_for_unrelated_tool():
    node = ActivateTaskNode()
    result = node(_state(tool_name="tasks__list"))
    assert result == {}


def test_noop_for_empty_tool():
    node = ActivateTaskNode()
    result = node(_state(tool_name=""))
    assert result == {}


# ── tasks__set_active ─────────────────────────────────────────────────────────

def test_set_active_missing_task_id():
    node = ActivateTaskNode()
    result = node(_state(tool_name="tasks__set_active", tool_input={}))
    assert result == {}


def test_set_active_activates_task(tmp_path):
    db = _make_tasks_db(tmp_path)
    with patch("langchain_learning.nodes.activate_task._cfg") as cfg:
        cfg.tasks_db = db
        cfg.memory_db = tmp_path / "MEMORY.sqlite"  # doesn't exist → empty memories
        node = ActivateTaskNode()
        result = node(_state(
            tool_name="tasks__set_active",
            tool_input={"task_id": "task01"},
        ))
    assert result["active_task_id"] == "task01"
    assert result["active_task_title"] == "Fix auth bug"
    assert result["task_stack"] == []


def test_set_active_pushes_existing_onto_stack(tmp_path):
    db = _make_tasks_db(tmp_path)
    with sqlite3.connect(str(db)) as conn:
        conn.execute("INSERT INTO open_tasks VALUES ('task02', 'New task', '', 'open', '', datetime('now'), NULL)")
        conn.commit()
    with patch("langchain_learning.nodes.activate_task._cfg") as cfg:
        cfg.tasks_db = db
        cfg.memory_db = tmp_path / "MEMORY.sqlite"
        node = ActivateTaskNode()
        result = node(_state(
            tool_name="tasks__set_active",
            tool_input={"task_id": "task02"},
            active_task_id="task01",
            task_stack=[],
        ))
    assert result["active_task_id"] == "task02"
    assert "task01" in result["task_stack"]


def test_set_active_task_not_found(tmp_path):
    db = _make_tasks_db(tmp_path)
    with patch("langchain_learning.nodes.activate_task._cfg") as cfg:
        cfg.tasks_db = db
        cfg.memory_db = tmp_path / "MEMORY.sqlite"
        node = ActivateTaskNode()
        result = node(_state(
            tool_name="tasks__set_active",
            tool_input={"task_id": "nonexistent"},
        ))
    # {} means the node ran and found no matching task — not a silent failure
    assert result == {}
    # Verify the existing task was NOT disturbed
    import sqlite3
    with sqlite3.connect(str(db)) as conn:
        row = conn.execute("SELECT status FROM open_tasks WHERE id='task01'").fetchone()
    assert row[0] == "open"


# ── tasks__pop_active ─────────────────────────────────────────────────────────

def test_pop_empty_stack_clears_active():
    node = ActivateTaskNode()
    result = node(_state(tool_name="tasks__pop_active", task_stack=[]))
    assert result["active_task_id"] == ""
    assert result["task_stack"] == []


def test_pop_restores_previous_task(tmp_path):
    db = _make_tasks_db(tmp_path)
    with patch("langchain_learning.nodes.activate_task._cfg") as cfg:
        cfg.tasks_db = db
        cfg.memory_db = tmp_path / "MEMORY.sqlite"
        node = ActivateTaskNode()
        result = node(_state(
            tool_name="tasks__pop_active",
            task_stack=["task01"],
        ))
    assert result["active_task_id"] == "task01"
    assert result["task_stack"] == []


# ── _lookup_task ──────────────────────────────────────────────────────────────

def test_lookup_task_returns_none_when_db_missing(tmp_path):
    with patch("langchain_learning.nodes.activate_task._cfg") as cfg:
        cfg.tasks_db = tmp_path / "missing.db"
        assert _lookup_task("task01") is None


def test_lookup_task_returns_none_for_unknown(tmp_path):
    db = _make_tasks_db(tmp_path)
    with patch("langchain_learning.nodes.activate_task._cfg") as cfg:
        cfg.tasks_db = db
        assert _lookup_task("nope") is None


def test_lookup_task_returns_title_body(tmp_path):
    db = _make_tasks_db(tmp_path)
    with patch("langchain_learning.nodes.activate_task._cfg") as cfg:
        cfg.tasks_db = db
        result = _lookup_task("task01")
    assert result == ("Fix auth bug", "body text", "", "")
