"""Tests for LogTaskEventsNode auto-completion detection."""
import pytest
from langchain_learning.nodes.log_task_events import _is_completion_signal, _TASK_DONE_PATTERN


@pytest.mark.parametrize("text", [
    "task:63b488ca done",
    "task:63b488ca done.",
    "task:aac1ff18 done",
])
def test_task_id_done_convention(text):
    assert _TASK_DONE_PATTERN.search(text), f"expected task:id done match for: {text!r}"


@pytest.mark.parametrize("text", [
    "task:63b488ca done",
    "task:aac1ff18 DONE",
    "hey task:63b488ca done — wrapping up",
])
def test_explicit_convention_matches(text):
    assert _is_completion_signal(text), f"expected match for: {text!r}"


@pytest.mark.parametrize("text", [
    # common progress updates that must NOT auto-close
    "done",
    "Task is done.",
    "all tests passing",
    "All tests passing, marking done",
    "Fixed the bug, works now",
    "Task complete",
    "marked done",
    "finished implementing the feature",
    "completed the refactor",
    # other non-signals
    "Let me check what's done so far",
    "Working on it",
    "Here is the plan",
    "I will fix this",
    "running tests",
    "the task is still open",
    "",
])
def test_non_signals_do_not_match(text):
    assert not _is_completion_signal(text), f"expected no match for: {text!r}"
