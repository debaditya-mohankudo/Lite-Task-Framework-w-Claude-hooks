"""Tests for hooks/gates.py — Gate ABC, concrete gate classes, registry, and check()."""
from collections import OrderedDict
from unittest.mock import patch

import pytest

from hooks.gates import (
    Gate, GateContext, ToolCall, GATES, check,
    IMessageSendGate, MailComposeGate,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(
    tool_name: str = "imessage__send",
    tool_input: dict | None = None,
    current_tools: list[str] | None = None,
    current_results: dict[str, dict] | None = None,
    session_tools: dict[str, list[str]] | None = None,
    session_prompt_ids: list[str] | None = None,
    prompt_id: str = "p1",
) -> GateContext:
    results = current_results or {}
    calls = [
        ToolCall(tool=t, prompt_id=prompt_id, tool_result=results.get(t, {}))
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
# GateContext helpers
# ---------------------------------------------------------------------------


def test_ctx_prev_tools():
    ctx = _ctx(
        session_tools={"p0": ["contacts__search", "confirm__send"]},
        session_prompt_ids=["p0", "p1"],
        prompt_id="p1",
    )
    it = ctx.prev_tools()
    assert next(it) == "confirm__send"
    assert next(it) == "contacts__search"
    assert next(it, None) is None

    ctx2 = _ctx(session_tools={}, session_prompt_ids=[], prompt_id="p1")
    assert next(ctx2.prev_tools(), None) is None


def test_ctx_called_this_session():
    ctx = _ctx(
        session_tools={"p0": ["contacts__search"]},
        session_prompt_ids=["p0", "p1"],
        prompt_id="p1",
    )
    assert ctx.called_this_session("contacts__search")
    assert not ctx.called_this_session("confirm__send")


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
# IMessageSendGate
# ---------------------------------------------------------------------------

def test_imessage_denied_without_contacts_search():
    # confirm__send is prev(1) but nothing is prev(2) — contacts__search missing
    ctx = _ctx("imessage__send", session_tools={"p1": ["confirm__send"]})
    deny, reason = IMessageSendGate().verify(ctx)
    assert deny is True
    assert "contacts__search" in reason


def test_imessage_denied_without_confirm_send():
    # prev_tool(1) is contacts__search, not confirm__send
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": ["contacts__search"]},
    )
    deny, reason = IMessageSendGate().verify(ctx)
    assert deny is True
    assert "confirm__send" in reason


def test_imessage_allowed_sequence():
    # contacts__search → confirm__send → imessage__send
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": ["contacts__search", "confirm__send"]},
    )
    deny, _ = IMessageSendGate().verify(ctx)
    assert deny is False


def test_imessage_denied_when_contacts_search_not_before_confirm():
    # confirm__send fired but contacts__search was not immediately before it
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": ["confirm__send"]},
    )
    deny, reason = IMessageSendGate().verify(ctx)
    assert deny is True
    assert "contacts__search" in reason


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
        session_tools={"p1": ["contacts__search"]},
        session_prompt_ids=["p1"],
        prompt_id="p1",
    )
    deny, _ = MailComposeGate().verify(ctx)
    assert deny is False


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
    assert "confirm__send" in reason


def test_check_mail_compose_denied_via_dispatch():
    ctx = _ctx("mail__compose")
    deny, reason = check("mail__compose", ctx)
    assert deny is True
    assert "contacts__search" in reason


