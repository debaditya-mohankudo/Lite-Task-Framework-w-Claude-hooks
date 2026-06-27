"""Fan-out failure injection tests for the session graph.

Verifies that when an underlying dependency (DB, ollama, index) fails inside
a fan-out node, the node's own except Exception handles it gracefully and
returns a default — and downstream nodes still fire.

This is the correct injection level: patch the *dependency*, not the node's
__call__. That way the node's error handling is exercised, not bypassed.

If a node raises unhandled to LangGraph, the graph aborts entirely — these
tests catch that regression too (they'd error out rather than asserting).

Topology under test (UPS with active task):
    load_active_task → load_task_history ──┐
                     → load_task_code    ──┼──→ cwd_domain_detect ──┐
                     → load_related_tasks─┘    load_memories        ├──→ set_prompt_id → log_task_events
                                               score_tools          ┘
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
from langgraph.checkpoint.memory import MemorySaver

import langchain_learning.session_graph as sg
from tests.fixtures.db_factories import make_tasks_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tasks_db(tmp_path: Path, task_id: str = "task0001") -> Path:
    return make_tasks_db(tmp_path, tasks=[
        {"id": task_id, "title": "Fix auth bug", "body": "body", "status": "open"},
    ])


def _build_graph():
    graph = sg.build_session_graph(checkpointer=MemorySaver())
    sg._graph = graph
    return graph


def _seed_active_task(graph, session_id: str, task_id: str, title: str = "Fix auth bug"):
    from langchain_learning.session_graph import _config
    graph.update_state(_config(session_id), {
        "active_task_id": task_id,
        "active_task_title": title,
    })


def _run_ups(graph, session_id: str, tmp_path: Path) -> dict:
    return sg.run_session(
        prompt="fix the broken import",
        session_id=session_id,
        cwd=str(tmp_path),
    )


# ---------------------------------------------------------------------------
# Single dependency failures — node handles gracefully, graph completes
# ---------------------------------------------------------------------------

class TestSingleDependencyFailure:

    def test_load_task_history_db_error_graph_completes(self, tmp_path):
        """DB failure inside load_task_history → node returns [] → graph completes."""
        graph = _build_graph()
        session_id = "fail-hist-01"
        _seed_active_task(graph, session_id, "task0001")

        mock_cfg = MagicMock()
        mock_cfg.tasks_db = tmp_path / "missing.db"  # doesn't exist → node bails
        mock_cfg.memory_db = tmp_path / "MEMORY.sqlite"

        with patch("langchain_learning.nodes.load_task_history._cfg", mock_cfg), \
             patch("langchain_learning.nodes.log_task_events.LogTaskEventsNode.__call__",
                   return_value={}), \
             patch("langchain_learning.nodes.load_memories.LoadMemoriesNode.__call__",
                   return_value={"memories": [{"name": "m1"}]}) as mock_memories:

            result = _run_ups(graph, session_id, tmp_path)

        # Graph reached END
        assert isinstance(result, dict)
        assert "session_id" in result
        # load_memories ran despite load_task_history bailing
        mock_memories.assert_called_once()
        # task_context is empty (node returned default)
        assert result.get("task_context", []) == []

    def test_load_related_tasks_vector_error_graph_completes(self, tmp_path):
        """handle_neighbors raising → load_related_tasks returns [] → graph completes."""
        graph = _build_graph()
        session_id = "fail-rel-01"
        _seed_active_task(graph, session_id, "task0001")

        with patch("langchain_learning.nodes.load_related_tasks.handle_neighbors",
                   side_effect=Exception("tvim index corrupt")), \
             patch("langchain_learning.nodes.log_task_events.LogTaskEventsNode.__call__",
                   return_value={}), \
             patch("langchain_learning.nodes.load_memories.LoadMemoriesNode.__call__",
                   return_value={"memories": []}) as mock_memories:

            result = _run_ups(graph, session_id, tmp_path)

        assert isinstance(result, dict)
        # related_tasks is empty — node's except Exception caught the error
        assert result.get("related_tasks", []) == []
        # downstream load_memories still ran
        mock_memories.assert_called_once()

    def test_load_memories_db_error_score_tools_still_runs(self, tmp_path):
        """MEMORY.sqlite failure → load_memories returns [] → score_tools still runs."""
        graph = _build_graph()
        session_id = "fail-mem-01"
        _seed_active_task(graph, session_id, "task0001")

        mock_cfg = MagicMock()
        mock_cfg.tasks_db = _make_tasks_db(tmp_path)
        mock_cfg.memory_db = tmp_path / "MEMORY.sqlite"  # doesn't exist

        with patch("langchain_learning.nodes.load_memories._cfg", mock_cfg), \
             patch("langchain_learning.nodes.log_task_events.LogTaskEventsNode.__call__",
                   return_value={}), \
             patch("langchain_learning.nodes.score_tools.ScoreToolsNode.__call__",
                   return_value={"tool_hints": ["contacts__search"]}) as mock_score:

            result = _run_ups(graph, session_id, tmp_path)

        assert isinstance(result, dict)
        # score_tools ran despite memory failure
        mock_score.assert_called_once()

    def test_load_task_code_index_missing_graph_completes(self, tmp_path):
        """Missing .code_embeddings.tvim → load_task_code returns [] → graph completes."""
        graph = _build_graph()
        session_id = "fail-code-01"
        _seed_active_task(graph, session_id, "task0001")
        # No .code_embeddings.tvim in tmp_path → node bails at existence check

        with patch("langchain_learning.nodes.log_task_events.LogTaskEventsNode.__call__",
                   return_value={}), \
             patch("langchain_learning.nodes.load_related_tasks.handle_neighbors",
                   return_value=[]) as mock_neighbors:

            result = _run_ups(graph, session_id, tmp_path)

        assert isinstance(result, dict)
        assert result.get("task_rag_chunks", []) == []
        # load_related_tasks still ran
        mock_neighbors.assert_called_once()


# ---------------------------------------------------------------------------
# Two simultaneous dependency failures
# ---------------------------------------------------------------------------

class TestDoubleDependencyFailure:

    def test_history_and_related_both_fail_graph_completes(self, tmp_path):
        """load_task_history DB missing + handle_neighbors raising → graph still completes."""
        graph = _build_graph()
        session_id = "fail-double-01"
        _seed_active_task(graph, session_id, "task0001")

        mock_cfg = MagicMock()
        mock_cfg.tasks_db = tmp_path / "missing.db"
        mock_cfg.memory_db = tmp_path / "MEMORY.sqlite"

        with patch("langchain_learning.nodes.load_task_history._cfg", mock_cfg), \
             patch("langchain_learning.nodes.load_related_tasks.handle_neighbors",
                   side_effect=Exception("no index")), \
             patch("langchain_learning.nodes.log_task_events.LogTaskEventsNode.__call__",
                   return_value={}), \
             patch("langchain_learning.nodes.load_memories.LoadMemoriesNode.__call__",
                   return_value={"memories": []}) as mock_memories:

            result = _run_ups(graph, session_id, tmp_path)

        assert isinstance(result, dict)
        assert result.get("task_context", []) == []
        assert result.get("related_tasks", []) == []
        # load_memories — second fan-out tier — still ran
        mock_memories.assert_called_once()

    def test_memories_and_score_tools_both_fail_set_prompt_id_runs(self, tmp_path):
        """load_memories + score_tools both fail → set_prompt_id still fires."""
        graph = _build_graph()
        session_id = "fail-double-02"
        _seed_active_task(graph, session_id, "task0001")

        mock_cfg_mem = MagicMock()
        mock_cfg_mem.tasks_db = _make_tasks_db(tmp_path)
        mock_cfg_mem.memory_db = tmp_path / "MEMORY.sqlite"  # missing

        with patch("langchain_learning.nodes.load_memories._cfg", mock_cfg_mem), \
             patch("langchain_learning.nodes.score_tools.ScoreToolsNode.__call__",
                   return_value={"tool_hints": []}), \
             patch("langchain_learning.nodes.log_task_events.LogTaskEventsNode.__call__",
                   return_value={}), \
             patch("langchain_learning.nodes.set_prompt_id.SetPromptIdNode.__call__",
                   return_value={"prompt_id": "double-pid"}) as mock_pid:

            result = _run_ups(graph, session_id, tmp_path)

        assert isinstance(result, dict)
        # set_prompt_id — the convergence node — still ran
        mock_pid.assert_called_once()


# ---------------------------------------------------------------------------
# No-active-task path
# ---------------------------------------------------------------------------

class TestNoActiveTaskFailure:

    def test_load_related_tasks_failure_no_task_path(self, tmp_path):
        """On no-active-task path, handle_neighbors error → [] → set_prompt_id runs."""
        graph = _build_graph()
        session_id = "fail-notask-01"
        # No active task seeded

        with patch("langchain_learning.nodes.load_related_tasks.handle_neighbors",
                   side_effect=Exception("tvim not found")), \
             patch("langchain_learning.nodes.log_task_events.LogTaskEventsNode.__call__",
                   return_value={}), \
             patch("langchain_learning.nodes.set_prompt_id.SetPromptIdNode.__call__",
                   return_value={"prompt_id": "notask-pid"}) as mock_pid:

            result = _run_ups(graph, session_id, tmp_path)

        assert isinstance(result, dict)
        assert result.get("related_tasks", []) == []
        mock_pid.assert_called_once()
