"""Tests for hooks/gates.py — Gate ABC, concrete gate classes, registry, and check()."""
import time
from collections import OrderedDict

import pytest

from hooks.gates import (
    Gate, GateContext, ToolCall, GATES, check,
    IMessageSendGate, MailComposeGate, MailDeleteGate,
    DEFAULT_WINDOW_S,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tc(tool: str, tool_input: dict | None = None, ts: float | None = None) -> dict:
    """Build a session_tools bucket entry. Defaults to a recent timestamp."""
    return {"tool": tool, "tool_input": tool_input or {}, "ts": ts if ts is not None else time.time()}


def _stale_ts() -> float:
    """Return a timestamp older than the staleness window."""
    return time.time() - DEFAULT_WINDOW_S - 10


def _ctx(
    tool_name: str = "imessage__send",
    tool_input: dict | None = None,
    current_tools: list[str] | None = None,
    session_tools: dict[str, list] | None = None,
    session_prompt_ids: list[str] | None = None,
    prompt_id: str = "p1",
    prompt_text: str = "",
) -> GateContext:
    calls = [
        ToolCall(tool=t, prompt_id=prompt_id)
        for t in (current_tools or [])
    ]
    return GateContext(
        tool_name=tool_name,
        tool_input=tool_input or {},
        current_calls=calls,
        session_tools=OrderedDict(session_tools or {}),
        session_prompt_ids=session_prompt_ids or [prompt_id],
        prompt_id=prompt_id,
        prompt_text=prompt_text,
    )


# ---------------------------------------------------------------------------
# Gate is ABC — cannot instantiate directly
# ---------------------------------------------------------------------------

def test_gate_is_abstract():
    with pytest.raises(TypeError):
        Gate()


# ---------------------------------------------------------------------------
# @prereq decorator — structural checks
# ---------------------------------------------------------------------------

def test_prereq_gates_are_instantiable():
    # Decorated gates must not remain abstract
    IMessageSendGate()
    MailComposeGate()
    MailDeleteGate()


def test_prereq_gates_preserve_tool_name():
    assert IMessageSendGate().tool_name == "imessage__send"
    assert MailComposeGate().tool_name == "mail__compose"
    assert MailDeleteGate().tool_name == "mail__delete"


def test_prereq_gates_are_gate_subclasses():
    assert isinstance(IMessageSendGate(), Gate)
    assert isinstance(MailComposeGate(), Gate)
    assert isinstance(MailDeleteGate(), Gate)


def test_prereq_gates_registered_in_registry():
    assert "imessage__send" in GATES
    assert "mail__compose" in GATES
    assert "mail__delete" in GATES


# ---------------------------------------------------------------------------
# GateContext.prev_tools — yields ToolCall objects
# ---------------------------------------------------------------------------

def test_ctx_prev_tools_yields_toolcall_objects():
    ctx = _ctx(
        session_tools={"p0": [_tc("contacts__search", {"name": "Alice"}), _tc("imessage__send")]},
        session_prompt_ids=["p0", "p1"],
        prompt_id="p1",
    )
    it = ctx.prev_tools()
    first = next(it)
    assert isinstance(first, ToolCall)
    assert first.tool == "imessage__send"
    second = next(it)
    assert second.tool == "contacts__search"
    assert second.tool_input == {"name": "Alice"}
    assert next(it, None) is None


def test_ctx_prev_tools_empty():
    ctx = _ctx(session_tools={}, session_prompt_ids=[], prompt_id="p1")
    assert next(ctx.prev_tools(), None) is None


# ---------------------------------------------------------------------------
# GateContext.called_this_session
# ---------------------------------------------------------------------------

def test_ctx_called_this_session():
    ctx = _ctx(
        session_tools={"p0": [_tc("contacts__search")]},
        session_prompt_ids=["p0", "p1"],
        prompt_id="p1",
    )
    assert ctx.called_this_session("contacts__search")
    assert not ctx.called_this_session("imessage__send")


# ---------------------------------------------------------------------------
# GateContext.called_recently
# ---------------------------------------------------------------------------

def test_ctx_called_recently_within_window():
    ctx = _ctx(
        session_tools={"p0": [_tc("contacts__search")]},
        session_prompt_ids=["p0", "p1"],
        prompt_id="p1",
    )
    assert ctx.called_recently("contacts__search", window_s=120.0)
    assert not ctx.called_recently("imessage__send", window_s=120.0)


def test_ctx_called_recently_stale():
    ctx = _ctx(
        session_tools={"p0": [_tc("contacts__search", ts=_stale_ts())]},
        session_prompt_ids=["p0", "p1"],
        prompt_id="p1",
    )
    assert not ctx.called_recently("contacts__search", window_s=120.0)


def test_ctx_called_recently_mixed_stale_and_fresh():
    # stale entry followed by a fresh one — should be allowed
    ctx = _ctx(
        session_tools={"p0": [
            _tc("contacts__search", ts=_stale_ts()),
            _tc("contacts__search"),
        ]},
        session_prompt_ids=["p0", "p1"],
        prompt_id="p1",
    )
    assert ctx.called_recently("contacts__search", window_s=120.0)


# ---------------------------------------------------------------------------
# GATES registry
# ---------------------------------------------------------------------------

def test_imessage_send_gate_exists():
    assert "imessage__send" in GATES
    assert isinstance(GATES["imessage__send"], IMessageSendGate)


def test_mail_compose_gate_exists():
    assert "mail__compose" in GATES
    assert isinstance(GATES["mail__compose"], MailComposeGate)


# ---------------------------------------------------------------------------
# IMessageSendGate — contacts__search within last 10 calls with name arg
# ---------------------------------------------------------------------------

def test_imessage_denied_no_prior_calls():
    ctx = _ctx("imessage__send")
    deny, reason = IMessageSendGate().verify(ctx)
    assert deny is True
    assert "contacts__search" in reason


def test_imessage_denied_contacts_search_without_name():
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": [_tc("contacts__search", {})]},
    )
    deny, reason = IMessageSendGate().verify(ctx)
    assert deny is True
    assert "contacts__search" in reason


def test_imessage_allowed_contacts_search_with_name_immediate():
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": [_tc("contacts__search", {"name": "Alice"})]},
        prompt_text="send message to Alice",
    )
    deny, _ = IMessageSendGate().verify(ctx)
    assert deny is False


def test_imessage_allowed_contacts_search_within_window():
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": [_tc("contacts__search", {"name": "Bob"})]},
        prompt_text="message Bob about the meeting",
    )
    deny, _ = IMessageSendGate().verify(ctx)
    assert deny is False


def test_imessage_allowed_no_prompt_text_skips_name_check():
    # prompt_text is empty — name check is skipped, gate passes on prereq alone
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": [_tc("contacts__search", {"name": "Alice"})]},
        prompt_text="",
    )
    deny, _ = IMessageSendGate().verify(ctx)
    assert deny is False


def test_imessage_denied_name_not_in_prompt():
    # contacts__search was for "Alice" but prompt mentions "Bob"
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": [_tc("contacts__search", {"name": "Alice"})]},
        prompt_text="send a message to Bob",
    )
    deny, reason = IMessageSendGate().verify(ctx)
    assert deny is True
    assert "Alice" in reason


def test_imessage_allowed_name_case_insensitive():
    # name check is case-insensitive
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": [_tc("contacts__search", {"name": "Alice"})]},
        prompt_text="Send iMessage to ALICE now",
    )
    deny, _ = IMessageSendGate().verify(ctx)
    assert deny is False


def test_imessage_allowed_name_substring_in_prompt():
    # "alice" appears as part of a longer word in the prompt
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": [_tc("contacts__search", {"name": "Alice Smith"})]},
        prompt_text="remind alice smith about tomorrow",
    )
    deny, _ = IMessageSendGate().verify(ctx)
    assert deny is False


def test_imessage_denied_contacts_search_stale():
    # contacts__search happened more than DEFAULT_WINDOW_S seconds ago — denied
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": [_tc("contacts__search", {"name": "Bob"}, ts=_stale_ts())]},
    )
    deny, reason = IMessageSendGate().verify(ctx)
    assert deny is True
    assert "contacts__search" in reason


def test_imessage_allowed_contacts_search_in_current_calls():
    ctx = _ctx(
        "imessage__send",
        current_tools=["contacts__search"],
    )
    # current_calls built without tool_input — name is empty, should deny
    deny, _ = IMessageSendGate().verify(ctx)
    assert deny is True  # no name arg in current_calls (built without it)


# ---------------------------------------------------------------------------
# MailComposeGate
# ---------------------------------------------------------------------------

def test_mail_compose_denied_without_contacts_search():
    ctx = _ctx("mail__compose")
    deny, reason = MailComposeGate().verify(ctx)
    assert deny is True
    assert "contacts__search" in reason


def test_mail_compose_allowed_after_contacts_search():
    ctx = _ctx(
        "mail__compose",
        session_tools={"p1": [_tc("contacts__search")]},
        session_prompt_ids=["p1"],
        prompt_id="p1",
    )
    deny, _ = MailComposeGate().verify(ctx)
    assert deny is False


# ---------------------------------------------------------------------------
# MailDeleteGate
# ---------------------------------------------------------------------------

def test_mail_delete_denied_without_mail_read():
    ctx = _ctx("mail__delete")
    deny, reason = MailDeleteGate().verify(ctx)
    assert deny is True
    assert "mail__read" in reason


def test_mail_delete_allowed_after_mail_read():
    ctx = _ctx(
        "mail__delete",
        session_tools={"p1": [_tc("mail__read")]},
    )
    deny, _ = MailDeleteGate().verify(ctx)
    assert deny is False


def test_mail_delete_allowed_mail_read_within_window():
    # mail__read happened recently — allowed
    ctx = _ctx(
        "mail__delete",
        session_tools={"p1": [_tc("mail__read")]},
    )
    deny, _ = MailDeleteGate().verify(ctx)
    assert deny is False


def test_mail_delete_denied_mail_read_stale():
    # mail__read happened more than DEFAULT_WINDOW_S seconds ago — denied
    ctx = _ctx(
        "mail__delete",
        session_tools={"p1": [_tc("mail__read", ts=_stale_ts())]},
    )
    deny, reason = MailDeleteGate().verify(ctx)
    assert deny is True
    assert "mail__read" in reason


# ---------------------------------------------------------------------------
# check() dispatch
# ---------------------------------------------------------------------------

def test_check_ungated_tool_always_allowed():
    ctx = _ctx("some__unknown_tool")
    deny, reason = check("some__unknown_tool", ctx)
    assert deny is False
    assert reason == ""


def test_check_imessage_denied_via_dispatch():
    ctx = _ctx("imessage__send")
    deny, reason = check("imessage__send", ctx)
    assert deny is True
    assert "contacts__search" in reason


def test_check_mail_compose_denied_via_dispatch():
    ctx = _ctx("mail__compose")
    deny, reason = check("mail__compose", ctx)
    assert deny is True
    assert "contacts__search" in reason


# ---------------------------------------------------------------------------
# tasks__create body format gate (_check_task_body_format in dispatcher.py)
# ---------------------------------------------------------------------------

from hooks.dispatcher import _check_task_body_format

_VALID_FIX_BODY = (
    "Task:\nAdd pre-tool hook\n\n"
    "Resolution:\nInject reminder via deny.\n\n"
    "Cause:\nNo enforcement existed.\n\n"
    "Files:\ndispatcher.py"
)

_VALID_FEATURE_BODY = (
    "Type: feature\n\n"
    "Task:\nAdd feature template to body format gate\n\n"
    "Design:\nBranch on Type: feature in body; require Type, Task, Design, Files.\n\n"
    "Files:\nhooks/dispatcher.py, tests/test_gates.py"
)


# --- fix template ---

def test_task_body_valid_fix_returns_none():
    assert _check_task_body_format({"body": _VALID_FIX_BODY}) is None


def test_task_body_empty_string_denied():
    result = _check_task_body_format({"body": ""})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "body" in result["hookSpecificOutput"]["permissionDecisionReason"].lower()


def test_task_body_missing_key_denied():
    result = _check_task_body_format({})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_task_body_missing_resolution_section():
    body = "Task:\nfoo\n\nCause:\nbar\n\nFiles:\nbaz.py"
    result = _check_task_body_format({"body": body})
    assert result is not None
    assert "Resolution:" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_task_body_missing_cause_and_files():
    body = "Task:\nfoo\n\nResolution:\nbar"
    result = _check_task_body_format({"body": body})
    reason = result["hookSpecificOutput"]["permissionDecisionReason"]
    assert "Cause:" in reason
    assert "Files:" in reason


def test_task_body_missing_task_section():
    body = "Resolution:\nbar\n\nCause:\nbaz\n\nFiles:\nfoo.py"
    result = _check_task_body_format({"body": body})
    assert "Task:" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_task_body_fix_deny_includes_format_template():
    result = _check_task_body_format({"body": ""})
    reason = result["hookSpecificOutput"]["permissionDecisionReason"]
    assert "Resolution:" in reason
    assert "Cause:" in reason


# --- feature template ---

def test_task_body_valid_feature_returns_none():
    assert _check_task_body_format({"body": _VALID_FEATURE_BODY}) is None


def test_task_body_feature_missing_design_section():
    body = "Type: feature\n\nTask:\nfoo\n\nFiles:\nbar.py"
    result = _check_task_body_format({"body": body})
    assert result is not None
    assert "Design:" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_task_body_feature_missing_files_section():
    body = "Type: feature\n\nTask:\nfoo\n\nDesign:\nbar"
    result = _check_task_body_format({"body": body})
    assert "Files:" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_task_body_feature_does_not_require_resolution_or_cause():
    # A valid feature body must NOT be rejected for missing Resolution/Cause
    result = _check_task_body_format({"body": _VALID_FEATURE_BODY})
    assert result is None


def test_task_body_feature_deny_includes_feature_format_template():
    body = "Type: feature\n\nTask:\nfoo"
    result = _check_task_body_format({"body": body})
    reason = result["hookSpecificOutput"]["permissionDecisionReason"]
    assert "Design:" in reason
    assert "Resolution:" not in reason
