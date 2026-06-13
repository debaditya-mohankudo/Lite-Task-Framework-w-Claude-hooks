"""Tests for hooks/dispatcher.py — pure functions only.

The _handle_* functions invoke LangGraph session graphs and are integration-level.
Tests here cover the pure extractors and validators that can be tested in isolation.
This is intentional: difficulty adding unit tests to the handlers is a known signal
that they carry too much orchestration logic (monolith smell — noted for future refactor).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure hooks/ is importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "hooks"))

from dispatcher import (
    _extract_prompt,
    _get_claude_session_id,
    _format_system_prompt,
    _check_task_body_format,
)


# ── _get_claude_session_id ────────────────────────────────────────────────────

def test_extracts_session_id():
    assert _get_claude_session_id({"session_id": "abc123"}) == "abc123"


def test_missing_session_id_returns_empty():
    assert _get_claude_session_id({}) == ""


# ── _extract_prompt ───────────────────────────────────────────────────────────

def test_extracts_top_level_prompt():
    assert _extract_prompt({"prompt": "hello"}) == "hello"


def test_extracts_prompt_from_message_string():
    result = _extract_prompt({"message": {"content": "hello from message"}})
    assert result == "hello from message"


def test_extracts_prompt_from_message_blocks():
    result = _extract_prompt({"message": {"content": [
        {"type": "text", "text": "block one "},
        {"type": "text", "text": "block two"},
    ]}})
    assert result == "block one block two"


def test_strips_xml_context_tags():
    result = _extract_prompt({"prompt": "<system_reminder>noise</system_reminder>\nreal prompt"})
    assert "noise" not in result
    assert "real prompt" in result


def test_returns_empty_when_no_prompt():
    assert _extract_prompt({}) == ""


# ── _format_system_prompt ─────────────────────────────────────────────────────

def _base_ctx(**kwargs) -> dict:
    base = {"session_id": "", "prompt_id": "", "domains": [], "memories": [],
            "tool_hints": [], "active_task_id": "", "active_task_title": "",
            "task_body": "", "mid_task_decisions": [], "task_memories": [],
            "task_context": [], "task_rag_chunks": [], "related_tasks": []}
    base.update(kwargs)
    return base


def test_empty_ctx_returns_empty_string():
    assert _format_system_prompt(_base_ctx()) == ""


def test_includes_turn_state_block():
    result = _format_system_prompt(_base_ctx(session_id="sess01", prompt_id="ppp1"))
    assert "## Turn state" in result
    assert "sess01" in result
    assert "ppp1" in result


def test_includes_active_domains():
    result = _format_system_prompt(_base_ctx(domains=["market-intel"]))
    assert "market-intel" in result


def test_includes_memories():
    mem = {"name": "my-mem", "domain": "global", "body": "remember this"}
    result = _format_system_prompt(_base_ctx(memories=[mem]))
    assert "## Injected memories" in result
    assert "remember this" in result


def test_includes_tool_hints():
    hint = {"tool_name": "tasks__create", "skill": "task-framework", "count": 5}
    result = _format_system_prompt(_base_ctx(tool_hints=[hint]))
    assert "## Suggested tools" in result
    assert "tasks__create" in result


def test_includes_active_task():
    result = _format_system_prompt(_base_ctx(
        active_task_id="abc123", active_task_title="Fix the bug", task_body="details"
    ))
    assert "## Active task" in result
    assert "abc123" in result
    assert "Fix the bug" in result


def test_includes_mid_task_decisions():
    result = _format_system_prompt(_base_ctx(mid_task_decisions=["use postgres"]))
    assert "## Task decisions" in result
    assert "use postgres" in result


def test_includes_related_tasks():
    result = _format_system_prompt(_base_ctx(
        related_tasks=[{"id": "t1", "title": "Prior task", "body_snippet": ""}]
    ))
    assert "## Related past tasks" in result
    assert "Prior task" in result


def test_includes_task_history_single_session():
    ctx = [{"session_id": "sess01", "turn": 3, "summary": "did stuff", "tools": "Bash"}]
    result = _format_system_prompt(_base_ctx(task_context=ctx))
    assert "## Task history" in result
    assert "turn 3" in result
    assert "did stuff" in result


def test_task_history_multi_session_shows_session_id():
    ctx = [
        {"session_id": "aaa", "turn": 1, "summary": "s1", "tools": ""},
        {"session_id": "bbb", "turn": 2, "summary": "s2", "tools": ""},
    ]
    result = _format_system_prompt(_base_ctx(task_context=ctx))
    assert "[aaa]" in result
    assert "[bbb]" in result


def test_includes_rag_chunks():
    chunk = {"name": "MyClass", "module": "src.tools.tasks", "file": "src/tools/tasks.py", "line": 42}
    result = _format_system_prompt(_base_ctx(task_rag_chunks=[chunk]))
    assert "## Relevant code" in result
    assert "MyClass" in result


# ── _check_task_body_format ───────────────────────────────────────────────────

def test_allows_valid_feature_body():
    body = "Type: feature\n\nTask: build x\n\nResolution: done\n\nMotivation: needed\n\nFiles: a.py"
    assert _check_task_body_format({"body": body}) is None


def test_allows_valid_bug_body():
    body = "Type: bug\n\nTask: broken\n\nResolution: fixed\n\nCause: null ptr\n\nFiles: b.py"
    assert _check_task_body_format({"body": body}) is None


def test_denies_empty_body():
    result = _check_task_body_format({"body": ""})
    assert result is not None
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_denies_missing_type_line():
    result = _check_task_body_format({"body": "Task: something\nResolution: done"})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "must start with 'Type:" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_denies_unknown_type():
    result = _check_task_body_format({"body": "Type: mystery\nTask: x"})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "Unknown task type" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_denies_missing_sections():
    body = "Type: bug\n\nTask: broken\n\nResolution: fixed"  # missing Cause and Files
    result = _check_task_body_format({"body": body})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "missing" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_allows_misc_type():
    body = "Type: misc\n\nTask: do x\n\nResolution: done\n\nNotes: context\n\nFiles: x.py"
    assert _check_task_body_format({"body": body}) is None
