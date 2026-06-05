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

def test_ctx_called_this_prompt():
    ctx = _ctx(current_tools=["contacts__search"])
    assert ctx.called_this_prompt("contacts__search")
    assert not ctx.called_this_prompt("confirm__send")


def test_ctx_called_prev_prompt():
    ctx = _ctx(
        session_tools={"p0": ["confirm__send"], "p1": []},
        session_prompt_ids=["p0", "p1"],
        prompt_id="p1",
    )
    assert ctx.called_prev_prompt("confirm__send")
    assert not ctx.called_prev_prompt("contacts__search")


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
    ctx = _ctx("imessage__send")
    deny, reason = IMessageSendGate().verify(ctx)
    assert deny is True
    assert "contacts__search" in reason


def test_imessage_denied_when_contacts_search_empty():
    ctx = _ctx(
        "imessage__send",
        current_tools=["contacts__search"],
        current_results={"contacts__search": []},
        session_tools={"p1": ["contacts__search"]},
        session_prompt_ids=["p1"],
        prompt_id="p1",
    )
    deny, reason = IMessageSendGate().verify(ctx)
    assert deny is True
    assert "no results" in reason


def test_imessage_denied_when_contacts_search_empty_dict():
    ctx = _ctx(
        "imessage__send",
        current_tools=["contacts__search"],
        current_results={"contacts__search": {"contacts": []}},
        session_tools={"p1": ["contacts__search"]},
        session_prompt_ids=["p1"],
        prompt_id="p1",
    )
    deny, reason = IMessageSendGate().verify(ctx)
    assert deny is True
    assert "no results" in reason


def test_imessage_denied_without_confirm_send():
    ctx = _ctx(
        "imessage__send",
        current_tools=["contacts__search"],
        current_results={"contacts__search": [{"name": "Alice", "phoneNumbers": [{"label": "mobile", "value": "+919876543210"}]}]},
        session_tools={"p1": ["contacts__search"]},
        session_prompt_ids=["p1"],
        prompt_id="p1",
    )
    deny, reason = IMessageSendGate().verify(ctx)
    assert deny is True
    assert "confirm__send" in reason


def test_imessage_allowed_when_both_prereqs_met_current_prompt():
    ctx = _ctx(
        "imessage__send",
        current_tools=["contacts__search", "confirm__send"],
        session_tools={"p1": ["contacts__search", "confirm__send"]},
    )
    deny, _ = IMessageSendGate().verify(ctx)
    assert deny is False


def test_imessage_allowed_when_confirm_send_prev_prompt():
    ctx = _ctx(
        "imessage__send",
        current_tools=["contacts__search"],
        current_results={"contacts__search": [{"name": "Alice", "phoneNumbers": [{"label": "mobile", "value": "+919876543210"}]}]},
        session_tools={"p0": ["contacts__search", "confirm__send"], "p1": ["contacts__search"]},
        session_prompt_ids=["p0", "p1"],
        prompt_id="p1",
    )
    deny, _ = IMessageSendGate().verify(ctx)
    assert deny is False


def test_imessage_no_tool_input_skips_phone_check():
    ctx = _ctx(
        "imessage__send",
        current_tools=["contacts__search", "confirm__send"],
        session_tools={"p1": ["contacts__search", "confirm__send"]},
    )
    deny, _ = IMessageSendGate().verify(ctx)
    assert deny is False


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
    assert "contacts__search" in reason


def test_check_mail_compose_denied_via_dispatch():
    ctx = _ctx("mail__compose")
    deny, reason = check("mail__compose", ctx)
    assert deny is True
    assert "contacts__search" in reason


