"""Tests for LoadRelatedTasksNode — vector semantic search via handle_neighbors."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from langchain_learning.nodes.load_related_tasks import LoadRelatedTasksNode


def _state(task_id: str = "aaaaaaaa") -> dict:
    return {"active_task_id": task_id, "active_task_title": "some task", "session_id": "test"}


def test_no_active_task_returns_empty():
    node = LoadRelatedTasksNode()
    result = node({"active_task_id": "", "active_task_title": "", "session_id": "test"})
    assert result == {"related_tasks": []}


def test_returns_top_3_done_tasks():
    neighbours = [
        {"task_id": "aaa", "title": "Task A", "status": "done",  "score": 0.9},
        {"task_id": "bbb", "title": "Task B", "status": "done",  "score": 0.8},
        {"task_id": "ccc", "title": "Task C", "status": "open",  "score": 0.7},
        {"task_id": "ddd", "title": "Task D", "status": "done",  "score": 0.6},
        {"task_id": "eee", "title": "Task E", "status": "wip",   "score": 0.5},
    ]
    with patch("langchain_learning.nodes.load_related_tasks.handle_neighbors", return_value=neighbours):
        node = LoadRelatedTasksNode()
        result = node(_state("aaaaaaaa"))

    related = result["related_tasks"]
    ids = [r["id"] for r in related]
    assert len(related) <= 3
    assert "aaa" in ids
    assert "bbb" in ids
    assert "ddd" in ids
    # non-done excluded
    assert "ccc" not in ids
    assert "eee" not in ids


def test_excludes_non_done_tasks():
    neighbours = [
        {"task_id": "open1", "title": "Open task", "status": "open", "score": 0.9},
        {"task_id": "wip01", "title": "WIP task",  "status": "wip",  "score": 0.8},
        {"task_id": "done1", "title": "Done task", "status": "done", "score": 0.7},
    ]
    with patch("langchain_learning.nodes.load_related_tasks.handle_neighbors", return_value=neighbours):
        node = LoadRelatedTasksNode()
        result = node(_state())

    ids = [r["id"] for r in result["related_tasks"]]
    assert "open1" not in ids
    assert "wip01" not in ids
    assert "done1" in ids


def test_handle_neighbors_error_returns_empty():
    with patch("langchain_learning.nodes.load_related_tasks.handle_neighbors", side_effect=Exception("ollama down")):
        node = LoadRelatedTasksNode()
        result = node(_state())
    assert result == {"related_tasks": []}


def test_empty_neighbors_returns_empty():
    with patch("langchain_learning.nodes.load_related_tasks.handle_neighbors", return_value=[]):
        node = LoadRelatedTasksNode()
        result = node(_state())
    assert result == {"related_tasks": []}
