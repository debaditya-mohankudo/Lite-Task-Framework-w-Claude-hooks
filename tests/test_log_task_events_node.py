"""Tests for LogTaskEventsNode — DB writes and auto-completion."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from langchain_learning.nodes.log_task_events import LogTaskEventsNode


def _make_tasks_db(tmp_path: Path) -> Path:
    db = tmp_path / "proj_tasks.db"
    with sqlite3.connect(str(db)) as conn:
        conn.execute("""
            CREATE TABLE open_tasks (
                id TEXT PRIMARY KEY, title TEXT, body TEXT,
                status TEXT DEFAULT 'open', tags TEXT DEFAULT '', updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT, prompt_id TEXT, session_id TEXT,
                turn INTEGER, summary TEXT, tools TEXT DEFAULT '',
                related TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("INSERT INTO open_tasks VALUES ('t1', 'My task', '', 'open', '', datetime('now'))")
        conn.commit()
    return db


def _state(**kwargs) -> dict:
    base = {
        "session_id": "sess0001",
        "active_task_id": "t1",
        "prompt_id": "ppp00001",
        "turn": 1,
        "prompt": "doing some work",
        "related_tasks": [],
    }
    base.update(kwargs)
    return base


def test_noop_when_no_active_task():
    node = LogTaskEventsNode()
    result = node(_state(active_task_id=""))
    assert result == {}


def test_noop_when_db_missing(tmp_path):
    with patch("langchain_learning.nodes.log_task_events._cfg") as cfg:
        cfg.tasks_db = tmp_path / "missing.db"
        node = LogTaskEventsNode()
        result = node(_state())
    assert result == {}


def test_inserts_task_event_row(tmp_path):
    db = _make_tasks_db(tmp_path)
    with patch("langchain_learning.nodes.log_task_events._cfg") as cfg:
        cfg.tasks_db = db
        node = LogTaskEventsNode()
        node(_state(prompt="some work", turn=3))

    with sqlite3.connect(str(db)) as conn:
        row = conn.execute("SELECT task_id, turn, summary FROM task_events").fetchone()
    assert row[0] == "t1"
    assert row[1] == 3
    assert "some work" in row[2]


def test_auto_completion_marks_task_done(tmp_path):
    db = tmp_path / "proj_tasks.db"
    task_id = "aabbccdd11"  # 10 hex chars — satisfies \b[a-f0-9]{6,}\b
    with sqlite3.connect(str(db)) as conn:
        conn.execute("""
            CREATE TABLE open_tasks (
                id TEXT PRIMARY KEY, title TEXT, body TEXT,
                status TEXT DEFAULT 'open', tags TEXT DEFAULT '', updated_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT, prompt_id TEXT, session_id TEXT,
                turn INTEGER, summary TEXT, tools TEXT DEFAULT '',
                related TEXT DEFAULT '', created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("INSERT INTO open_tasks VALUES (?, 'My task', '', 'open', '', datetime('now'))", (task_id,))
        conn.commit()

    with patch("langchain_learning.nodes.log_task_events._cfg") as cfg, \
         patch("langchain_learning.nodes.log_task_events._clear_checkpoint"):
        cfg.tasks_db = db
        node = LogTaskEventsNode()
        result = node(_state(active_task_id=task_id, prompt=f"task:{task_id} done — wrapping up"))

    assert result["active_task_id"] == ""
    with sqlite3.connect(str(db)) as conn:
        status = conn.execute("SELECT status FROM open_tasks WHERE id=?", (task_id,)).fetchone()[0]
    assert status == "done"


def test_normal_prompt_does_not_close_task(tmp_path):
    db = _make_tasks_db(tmp_path)
    with patch("langchain_learning.nodes.log_task_events._cfg") as cfg:
        cfg.tasks_db = db
        node = LogTaskEventsNode()
        result = node(_state(prompt="just doing work"))

    assert result == {}
    with sqlite3.connect(str(db)) as conn:
        status = conn.execute("SELECT status FROM open_tasks WHERE id='t1'").fetchone()[0]
    assert status == "open"


def test_two_turns_insert_two_rows(tmp_path):
    db = _make_tasks_db(tmp_path)
    with patch("langchain_learning.nodes.log_task_events._cfg") as cfg:
        cfg.tasks_db = db
        node = LogTaskEventsNode()
        node(_state(prompt_id="p1", turn=1))
        node(_state(prompt_id="p2", turn=2))

    with sqlite3.connect(str(db)) as conn:
        count = conn.execute("SELECT COUNT(*) FROM task_events").fetchone()[0]
    assert count == 2
