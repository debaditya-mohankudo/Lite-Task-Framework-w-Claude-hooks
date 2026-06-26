"""Tests for ActivateTaskNode — PostToolUse bridge for task activation."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from langchain_learning.nodes.activate_task import (
    ActivateTaskNode,
    _lookup_task,
    _score_memories,
    _parse_files_section,
    _file_tokens,
    _backfill_memory_files,
)


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


# ── _parse_files_section ──────────────────────────────────────────────────────

def test_parse_files_section_comma_separated():
    body = "Type: feature\nTask:\ndesc\n\nFiles:\nhooks/gates.py, src/tools/memory.py\n\nResolution:\n"
    assert _parse_files_section(body) == ["hooks/gates.py", "src/tools/memory.py"]


def test_parse_files_section_newline_separated():
    body = "Type: feature\nTask:\ndesc\n\nFiles:\nhooks/gates.py\nsrc/tools/memory.py\n"
    result = _parse_files_section(body)
    assert "hooks/gates.py" in result
    assert "src/tools/memory.py" in result


def test_parse_files_section_missing():
    body = "Type: feature\nTask:\ndesc\n\nResolution:\nnone"
    assert _parse_files_section(body) == []


# ── _file_tokens ──────────────────────────────────────────────────────────────

def test_file_tokens_extracts_stem():
    tokens = _file_tokens(["hooks/gates.py"])
    assert "gate" in tokens or "gates" in tokens
    # directory components are intentionally excluded — too generic
    assert "hook" not in tokens and "hooks" not in tokens


def test_file_tokens_empty():
    assert _file_tokens([]) == set()


# ── _backfill_memory_files ────────────────────────────────────────────────────

_MEMORY_DDL = """
    CREATE TABLE memories (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT UNIQUE NOT NULL,
        type TEXT NOT NULL,
        domain TEXT DEFAULT 'global',
        tags TEXT DEFAULT '',
        body TEXT DEFAULT '',
        updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_validated TIMESTAMP,
        files TEXT,
        docs TEXT
    )
"""


def _make_memory_db(tmp_path: Path, memories: list[dict]) -> Path:
    db = tmp_path / "MEMORY.sqlite"
    with sqlite3.connect(str(db)) as conn:
        conn.execute(_MEMORY_DDL)
        for m in memories:
            conn.execute(
                "INSERT INTO memories (name, type, domain, tags, files) VALUES (?,?,?,?,?)",
                (m["name"], m.get("type", "feedback"), m.get("domain", "global"),
                 m.get("tags", ""), m.get("files", None)),
            )
        conn.commit()
    return db


def test_backfill_updates_matching_memory(tmp_path):
    db = _make_memory_db(tmp_path, [
        {"name": "claude-hooks-gate-framework", "domain": "claude-hooks", "tags": "gate, gates, hooks"},
    ])
    body = "Type: feature\nTask:\ndesc\n\nFiles:\nhooks/gates.py\n"
    with patch("langchain_learning.nodes.activate_task._cfg") as cfg:
        cfg.memory_db = db
        count = _backfill_memory_files("task01", body, "claude-hooks")
    assert count == 1
    with sqlite3.connect(str(db)) as conn:
        row = conn.execute("SELECT files FROM memories WHERE name='claude-hooks-gate-framework'").fetchone()
    assert row[0] == "hooks/gates.py"


def test_backfill_skips_already_filled_memory(tmp_path):
    db = _make_memory_db(tmp_path, [
        {"name": "claude-hooks-gate-framework", "domain": "claude-hooks",
         "tags": "gate gates", "files": "hooks/gates.py"},
    ])
    body = "Type: feature\nTask:\ndesc\n\nFiles:\nhooks/gates.py\n"
    with patch("langchain_learning.nodes.activate_task._cfg") as cfg:
        cfg.memory_db = db
        count = _backfill_memory_files("task01", body, "claude-hooks")
    assert count == 0


def test_backfill_no_overlap_skips(tmp_path):
    db = _make_memory_db(tmp_path, [
        {"name": "claude-hooks-unrelated-memory", "domain": "claude-hooks", "tags": "auth login"},
    ])
    body = "Type: feature\nTask:\ndesc\n\nFiles:\nhooks/gates.py\n"
    with patch("langchain_learning.nodes.activate_task._cfg") as cfg:
        cfg.memory_db = db
        count = _backfill_memory_files("task01", body, "claude-hooks")
    assert count == 0


def test_backfill_no_files_section_returns_zero(tmp_path):
    db = _make_memory_db(tmp_path, [
        {"name": "some-memory", "domain": "claude-hooks", "tags": "gate"},
    ])
    body = "Type: feature\nTask:\ndesc\n\nResolution:\nnone\n"
    with patch("langchain_learning.nodes.activate_task._cfg") as cfg:
        cfg.memory_db = db
        count = _backfill_memory_files("task01", body, "claude-hooks")
    assert count == 0


def test_backfill_skipped_for_replay_session(tmp_path):
    db_tasks = tmp_path / "proj_tasks.db"
    with sqlite3.connect(str(db_tasks)) as conn:
        conn.execute("""
            CREATE TABLE open_tasks (
                id TEXT PRIMARY KEY, title TEXT, body TEXT,
                status TEXT DEFAULT 'open', tags TEXT DEFAULT '',
                updated_at TEXT, parent_id TEXT DEFAULT NULL
            )
        """)
        conn.execute(
            "INSERT INTO open_tasks VALUES (?,?,?,?,?,?,?)",
            ("task01", "Fix gate", "Type: feature\nTask:\ndesc\n\nFiles:\nhooks/gates.py\n",
             "open", "project:claude-hooks", "2026-01-01", None),
        )
        conn.commit()
    db_mem = _make_memory_db(tmp_path, [
        {"name": "claude-hooks-gate-framework", "domain": "claude-hooks", "tags": "gate gates"},
    ])
    with patch("langchain_learning.nodes.activate_task._cfg") as cfg:
        cfg.tasks_db = db_tasks
        cfg.memory_db = db_mem
        node = ActivateTaskNode()
        result = node(_state(
            tool_name="tasks__set_active",
            tool_input={"task_id": "task01"},
            session_id="replay-abc123",
        ))
    assert result.get("active_task_id") == "task01"
    with sqlite3.connect(str(db_mem)) as conn:
        row = conn.execute("SELECT files FROM memories WHERE name='claude-hooks-gate-framework'").fetchone()
    assert row[0] is None  # guard skipped the write


def test_backfill_missing_memory_db_returns_zero(tmp_path):
    body = "Type: feature\nTask:\ndesc\n\nFiles:\nhooks/gates.py\n"
    with patch("langchain_learning.nodes.activate_task._cfg") as cfg:
        cfg.memory_db = tmp_path / "nonexistent.sqlite"
        count = _backfill_memory_files("task01", body, "claude-hooks")
    assert count == 0
