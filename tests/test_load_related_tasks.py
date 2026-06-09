"""Tests for LoadRelatedTasksNode — BM25 overlap scoring against done tasks."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

import langchain_learning.nodes.load_related_tasks as _mod
from langchain_learning.nodes.load_related_tasks import LoadRelatedTasksNode, _tokenise


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tasks_db(path: Path, rows: list[dict]) -> None:
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE open_tasks (
            id TEXT PRIMARY KEY, title TEXT, body TEXT,
            status TEXT DEFAULT 'open', tags TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    for r in rows:
        conn.execute(
            "INSERT INTO open_tasks (id, title, body, status, tags) VALUES (?,?,?,?,?)",
            (r["id"], r.get("title", ""), r.get("body", ""), r.get("status", "open"), r.get("tags", "")),
        )
    conn.commit()
    conn.close()


def _state(task_id: str = "aaaaaaaa", task_title: str = "") -> dict:
    return {"active_task_id": task_id, "active_task_title": task_title, "session_id": "test"}


# ---------------------------------------------------------------------------
# _tokenise
# ---------------------------------------------------------------------------

def test_tokenise_strips_short_tokens():
    tokens = _tokenise("the and for BM25 scoring overlap")
    assert "the" not in tokens
    assert "and" not in tokens
    assert "bm25" in tokens
    assert "scoring" in tokens
    assert "overlap" in tokens


def test_tokenise_stopwords_excluded():
    tokens = _tokenise("handle tool saturation with decay")
    assert "with" not in tokens
    assert "tool" in tokens
    assert "saturation" in tokens
    assert "decay" in tokens


# ---------------------------------------------------------------------------
# LoadRelatedTasksNode
# ---------------------------------------------------------------------------

def test_no_active_task_returns_empty():
    node = LoadRelatedTasksNode()
    result = node(_state(task_id="", task_title=""))
    assert result == {"related_tasks": []}


def test_missing_db_returns_empty(tmp_path):
    node = LoadRelatedTasksNode()
    token = _mod._TASKS_DB
    _mod._TASKS_DB = tmp_path / "nonexistent.db"
    try:
        result = node(_state(task_title="observability hook logs latency"))
        assert result == {"related_tasks": []}
    finally:
        _mod._TASKS_DB = token


def test_returns_top_3_by_overlap(tmp_path):
    db = tmp_path / "proj_tasks.db"
    _make_tasks_db(db, [
        {"id": "aaa00001", "title": "observability digest agent hook logs", "status": "done", "tags": "logs,hooks,latency,anomalies"},
        {"id": "aaa00002", "title": "observability hook latency spikes", "status": "done", "tags": "latency,hooks"},
        {"id": "aaa00003", "title": "gate denials hook logs analysis", "status": "done", "tags": "gate,logs,hooks"},
        {"id": "aaa00004", "title": "unrelated portfolio database schema", "status": "done", "tags": "portfolio,sqlite"},
        {"id": "aaa00005", "title": "another unrelated memory scoring", "status": "done", "tags": "memory,bm25"},
    ])

    node = LoadRelatedTasksNode()
    token = _mod._TASKS_DB
    _mod._TASKS_DB = db
    try:
        result = node(_state(task_title="observability digest hook logs latency anomalies gate denials"))
        related = result["related_tasks"]
    finally:
        _mod._TASKS_DB = token

    assert len(related) <= 3
    ids = [t["id"] for t in related]
    # top scorers should be the three observability/hook/logs tasks
    assert "aaa00001" in ids
    assert "aaa00002" in ids
    assert "aaa00003" in ids
    # unrelated tasks should not appear
    assert "aaa00004" not in ids


def test_excludes_active_task_itself(tmp_path):
    db = tmp_path / "proj_tasks.db"
    _make_tasks_db(db, [
        {"id": "active01", "title": "observability hook logs latency", "status": "done", "tags": "hooks,logs"},
        {"id": "other001", "title": "observability digest hook logs", "status": "done", "tags": "hooks,logs"},
    ])

    node = LoadRelatedTasksNode()
    token = _mod._TASKS_DB
    _mod._TASKS_DB = db
    try:
        result = node(_state(task_id="active01", task_title="observability hook logs latency"))
        related = result["related_tasks"]
    finally:
        _mod._TASKS_DB = token

    ids = [t["id"] for t in related]
    assert "active01" not in ids


def test_excludes_non_done_tasks(tmp_path):
    db = tmp_path / "proj_tasks.db"
    _make_tasks_db(db, [
        {"id": "open0001", "title": "observability hook logs digest", "status": "open", "tags": "hooks,logs"},
        {"id": "wip00001", "title": "observability hook logs digest", "status": "wip",  "tags": "hooks,logs"},
        {"id": "done0001", "title": "observability hook logs digest", "status": "done", "tags": "hooks,logs"},
    ])

    node = LoadRelatedTasksNode()
    token = _mod._TASKS_DB
    _mod._TASKS_DB = db
    try:
        result = node(_state(task_title="observability hook logs digest"))
        related = result["related_tasks"]
    finally:
        _mod._TASKS_DB = token

    ids = [t["id"] for t in related]
    assert "open0001" not in ids
    assert "wip00001" not in ids
    assert "done0001" in ids


def test_body_snippet_truncated(tmp_path):
    db = tmp_path / "proj_tasks.db"
    long_body = "observability hook logs " + ("x" * 300)
    _make_tasks_db(db, [
        {"id": "body0001", "title": "observability hook logs", "body": long_body, "status": "done", "tags": "hooks"},
    ])

    node = LoadRelatedTasksNode()
    token = _mod._TASKS_DB
    _mod._TASKS_DB = db
    try:
        result = node(_state(task_title="observability hook logs"))
        related = result["related_tasks"]
    finally:
        _mod._TASKS_DB = token

    assert len(related) == 1
    assert len(related[0]["body_snippet"]) <= 200


def test_no_overlap_returns_empty(tmp_path):
    db = tmp_path / "proj_tasks.db"
    _make_tasks_db(db, [
        {"id": "zzzz0001", "title": "portfolio database sqlite schema", "status": "done", "tags": "portfolio"},
    ])

    node = LoadRelatedTasksNode()
    token = _mod._TASKS_DB
    _mod._TASKS_DB = db
    try:
        result = node(_state(task_title="observability hook logs latency"))
        related = result["related_tasks"]
    finally:
        _mod._TASKS_DB = token

    assert related == []
