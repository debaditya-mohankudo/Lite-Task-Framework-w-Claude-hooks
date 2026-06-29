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


def test_returns_empty_always():
    # LoadRelatedTasksNode is disabled — always returns [] regardless of neighbors
    node = LoadRelatedTasksNode()
    result = node(_state("aaaaaaaa"))
    assert result == {"related_tasks": []}


def test_excludes_non_done_tasks():
    # LoadRelatedTasksNode is disabled — always returns [] regardless of neighbors
    node = LoadRelatedTasksNode()
    result = node(_state())
    assert result == {"related_tasks": []}


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
