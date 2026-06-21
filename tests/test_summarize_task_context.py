"""Tests for SummarizeTaskContextNode."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from langchain_learning.nodes.summarize_task_context import SummarizeTaskContextNode, _build_raw_context


def _state(**overrides) -> dict:
    base = {
        "active_task_id": "aabbccdd",
        "active_task_title": "fix the thing",
        "session_id": "test-session",
        "task_context": [],
        "task_rag_chunks": [],
        "related_tasks": [],
        "related_commits": [],
    }
    base.update(overrides)
    return base


def _long_context(n: int = 10) -> list[dict]:
    return [
        {"turn": i, "summary": f"did step {i} " * 15, "tools": "Read,Edit", "session_id": "test"}
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# _build_raw_context
# ---------------------------------------------------------------------------

def test_build_raw_context_empty():
    assert _build_raw_context(_state()) == ""


def test_build_raw_context_task_history():
    state = _state(task_context=[
        {"turn": 1, "summary": "fixed the bug", "tools": "Edit", "session_id": "abc12345"},
    ])
    raw = _build_raw_context(state)
    assert "Task history" in raw
    assert "fixed the bug" in raw
    assert "Edit" in raw


def test_build_raw_context_multi_session_shows_sid():
    state = _state(task_context=[
        {"turn": 1, "summary": "step one", "tools": "", "session_id": "aaaaaaaa"},
        {"turn": 2, "summary": "step two", "tools": "", "session_id": "bbbbbbbb"},
    ])
    raw = _build_raw_context(state)
    assert "[aaaaaaaa]" in raw
    assert "[bbbbbbbb]" in raw


def test_build_raw_context_single_session_no_sid():
    state = _state(task_context=[
        {"turn": 1, "summary": "step one", "tools": "", "session_id": "aaaaaaaa"},
        {"turn": 2, "summary": "step two", "tools": "", "session_id": "aaaaaaaa"},
    ])
    raw = _build_raw_context(state)
    assert "[aaaaaaaa]" not in raw


def test_build_raw_context_includes_all_sources():
    state = _state(
        task_context=[{"turn": 1, "summary": "done x", "tools": "", "session_id": "s"}],
        task_rag_chunks=[{"name": "MyFunc", "module": "mod", "file": "src/x.py", "line": 10}],
        related_tasks=[{"id": "abc123", "title": "Prior task", "body_snippet": "did y"}],
        related_commits=[{"commit_hash": "deadbeef", "file": "src/y.py", "score": 0.85}],
    )
    raw = _build_raw_context(state)
    assert "Task history" in raw
    assert "Relevant code" in raw
    assert "Related past tasks" in raw
    assert "Related commits" in raw
    assert "deadbeef" in raw
    assert "Prior task" in raw


# ---------------------------------------------------------------------------
# SummarizeTaskContextNode
# ---------------------------------------------------------------------------

def test_no_active_task_returns_empty():
    node = SummarizeTaskContextNode()
    result = node(_state(active_task_id=""))
    assert result == {"task_context_summary": ""}


def test_below_threshold_skips():
    # short context — well under 800 chars
    node = SummarizeTaskContextNode()
    state = _state(task_context=[{"turn": 1, "summary": "tiny", "tools": "", "session_id": "s"}])
    result = node(state)
    assert result == {"task_context_summary": ""}


def test_above_threshold_calls_agent(tmp_path):
    node = SummarizeTaskContextNode()
    state = _state(task_context=_long_context(10))

    mock_agent = MagicMock()
    mock_agent.invoke.return_value = "• done step 1\n• done step 2"

    # Redirect the vault dir to tmp so the success path never touches the real vault.
    with patch("langchain_learning.nodes.summarize_task_context.BareClaudeAgent", return_value=mock_agent), \
         patch("langchain_learning.nodes.summarize_task_context._TASK_CONTEXTS_DIR", tmp_path):
        result = node(state)

    assert result["task_context_summary"] == "• done step 1\n• done step 2"
    mock_agent.invoke.assert_called_once()
    call_arg = mock_agent.invoke.call_args[0][0]
    assert "Summarize the following task context" in call_arg


def test_agent_error_falls_back():
    node = SummarizeTaskContextNode()
    state = _state(task_context=_long_context(10))

    mock_agent = MagicMock()
    mock_agent.invoke.side_effect = RuntimeError("claude crashed")

    with patch("langchain_learning.nodes.summarize_task_context.BareClaudeAgent", return_value=mock_agent):
        result = node(state)

    assert result == {"task_context_summary": ""}


def test_timeout_falls_back(monkeypatch):
    import threading

    node = SummarizeTaskContextNode()
    state = _state(task_context=_long_context(10))

    mock_agent = MagicMock()

    def _slow_invoke(_prompt):
        import time
        time.sleep(60)

    mock_agent.invoke.side_effect = _slow_invoke

    # Patch timeout to 0.1s so the test doesn't actually wait 6s
    monkeypatch.setattr(
        "langchain_learning.nodes.summarize_task_context._TIMEOUT_SECONDS", 0.1
    )

    with patch("langchain_learning.nodes.summarize_task_context.BareClaudeAgent", return_value=mock_agent):
        result = node(state)

    assert result == {"task_context_summary": ""}
