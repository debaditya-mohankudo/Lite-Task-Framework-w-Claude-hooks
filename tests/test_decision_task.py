"""Tests for DecisionTaskNode — PostToolUse bridge for tasks__add_decision."""
from __future__ import annotations

from langchain_learning.nodes.decision_task import DecisionTaskNode


def _state(**kwargs) -> dict:
    base = {"session_id": "sess0001", "tool_name": "", "tool_input": {}, "mid_task_decisions": []}
    base.update(kwargs)
    return base


def test_noop_for_unrelated_tool():
    node = DecisionTaskNode()
    assert node(_state(tool_name="tasks__list")) == {}


def test_noop_for_empty_tool():
    node = DecisionTaskNode()
    assert node(_state()) == {}


def test_noop_for_empty_decision():
    node = DecisionTaskNode()
    result = node(_state(
        tool_name="tasks__add_decision",
        tool_input={"task_id": "abc", "decision": "   "},
    ))
    assert result == {}


def test_appends_decision_to_empty_list():
    node = DecisionTaskNode()
    result = node(_state(
        tool_name="tasks__add_decision",
        tool_input={"task_id": "abc", "decision": "Use postgres not sqlite"},
        mid_task_decisions=[],
    ))
    assert result["mid_task_decisions"] == ["Use postgres not sqlite"]


def test_appends_to_existing_decisions():
    node = DecisionTaskNode()
    result = node(_state(
        tool_name="tasks__add_decision",
        tool_input={"task_id": "abc", "decision": "Second decision"},
        mid_task_decisions=["First decision"],
    ))
    assert result["mid_task_decisions"] == ["First decision", "Second decision"]


def test_does_not_mutate_original_list():
    original = ["First decision"]
    node = DecisionTaskNode()
    node(_state(
        tool_name="tasks__add_decision",
        tool_input={"task_id": "abc", "decision": "Second decision"},
        mid_task_decisions=original,
    ))
    assert original == ["First decision"]


def test_strips_whitespace_from_decision():
    node = DecisionTaskNode()
    result = node(_state(
        tool_name="tasks__add_decision",
        tool_input={"task_id": "abc", "decision": "  trimmed  "},
        mid_task_decisions=[],
    ))
    assert result["mid_task_decisions"] == ["trimmed"]
