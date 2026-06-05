"""Tests for hooks/gates.py — Gate ABC, concrete gate classes, registry, and check()."""
from collections import OrderedDict

import pytest

from hooks.gates import (
    Gate, GateContext, ToolCall, GATES, check,
    IMessageSendGate, MailComposeGate, MailDeleteGate,
    _CONTACTS_SEARCH_WINDOW,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tc(tool: str, tool_input: dict | None = None) -> dict:
    """Build a session_tools bucket entry."""
    return {"tool": tool, "tool_input": tool_input or {}}


def _ctx(
    tool_name: str = "imessage__send",
    tool_input: dict | None = None,
    current_tools: list[str] | None = None,
    session_tools: dict[str, list] | None = None,
    session_prompt_ids: list[str] | None = None,
    prompt_id: str = "p1",
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
    )


# ---------------------------------------------------------------------------
# Gate is ABC — cannot instantiate directly
# ---------------------------------------------------------------------------

def test_gate_is_abstract():
    with pytest.raises(TypeError):
        Gate()


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
    )
    deny, _ = IMessageSendGate().verify(ctx)
    assert deny is False


def test_imessage_allowed_contacts_search_within_window():
    # contacts__search is the last in the window — still allowed
    other = [_tc(f"some__tool_{i}") for i in range(_CONTACTS_SEARCH_WINDOW - 1)]
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": [_tc("contacts__search", {"name": "Bob"})] + other},
    )
    deny, _ = IMessageSendGate().verify(ctx)
    assert deny is False


def test_imessage_denied_contacts_search_beyond_window():
    # contacts__search is one beyond the window
    other = [_tc(f"some__tool_{i}") for i in range(_CONTACTS_SEARCH_WINDOW)]
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": [_tc("contacts__search", {"name": "Bob"})] + other},
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


def test_mail_delete_denied_mail_read_not_immediate():
    ctx = _ctx(
        "mail__delete",
        session_tools={"p1": [_tc("mail__read"), _tc("some__other_tool")]},
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
