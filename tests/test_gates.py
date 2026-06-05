"""Tests for hooks/gates.py — Gate ABC, concrete gate classes, registry, and check()."""
import sqlite3
import tempfile
from collections import OrderedDict
from pathlib import Path
from unittest.mock import patch

import pytest

from hooks.gates import (
    Gate, GateContext, ToolCall, GATES, check,
    IMessageSendGate, MailComposeGate,
    _is_phone_number, _number_in_contacts,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ctx(
    tool_name: str = "imessage__send",
    tool_input: dict | None = None,
    current_tools: list[str] | None = None,
    session_tools: dict[str, list[str]] | None = None,
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


def test_imessage_denied_without_confirm_send():
    ctx = _ctx(
        "imessage__send",
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
        session_tools={"p0": ["contacts__search", "confirm__send"], "p1": []},
        session_prompt_ids=["p0", "p1"],
        prompt_id="p1",
    )
    deny, _ = IMessageSendGate().verify(ctx)
    assert deny is False


def test_imessage_denied_invalid_phone_number():
    ctx = _ctx(
        "imessage__send",
        tool_input={"recipient": "John Doe"},
        current_tools=["contacts__search", "confirm__send"],
        session_tools={"p1": ["contacts__search", "confirm__send"]},
    )
    deny, reason = IMessageSendGate().verify(ctx)
    assert deny is True
    assert "not a valid phone number" in reason


def test_imessage_denied_number_not_in_contacts():
    ctx = _ctx(
        "imessage__send",
        tool_input={"recipient": "+919876543210"},
        current_tools=["contacts__search", "confirm__send"],
        session_tools={"p1": ["contacts__search", "confirm__send"]},
    )
    with patch("hooks.gates._number_in_contacts", return_value=False):
        deny, reason = IMessageSendGate().verify(ctx)
    assert deny is True
    assert "not in your contacts" in reason


def test_imessage_allowed_number_in_contacts():
    ctx = _ctx(
        "imessage__send",
        tool_input={"recipient": "+919876543210"},
        current_tools=["contacts__search", "confirm__send"],
        session_tools={"p1": ["contacts__search", "confirm__send"]},
    )
    with patch("hooks.gates._number_in_contacts", return_value=True):
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


# ---------------------------------------------------------------------------
# _is_phone_number
# ---------------------------------------------------------------------------

def test_is_phone_number_valid_10_digits():
    assert _is_phone_number("9876543210")


def test_is_phone_number_valid_with_country_code():
    assert _is_phone_number("+919876543210")


def test_is_phone_number_valid_formatted():
    assert _is_phone_number("(987) 654-3210")


def test_is_phone_number_too_short():
    assert not _is_phone_number("12345")


def test_is_phone_number_too_long():
    assert not _is_phone_number("12345678901234")


def test_is_phone_number_name_not_number():
    assert not _is_phone_number("John Doe")


def test_is_phone_number_email():
    assert not _is_phone_number("foo@bar.com")


# ---------------------------------------------------------------------------
# _number_in_contacts — mocked AddressBook
# ---------------------------------------------------------------------------

def _make_addressbook_db(numbers: list[str]) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".abcddb", delete=False)
    con = sqlite3.connect(tmp.name)
    con.execute("CREATE TABLE ZABCDPHONENUMBER (ZFULLNUMBER TEXT)")
    for n in numbers:
        con.execute("INSERT INTO ZABCDPHONENUMBER VALUES (?)", (n,))
    con.commit()
    con.close()
    return Path(tmp.name)


def test_number_in_contacts_found(tmp_path):
    db = _make_addressbook_db(["+91 98765 43210"])
    with patch("hooks.gates.Path.home") as mock_home:
        mock_home.return_value = tmp_path
        ab_dir = tmp_path / "Library/Application Support/AddressBook/Sources/fake-source"
        ab_dir.mkdir(parents=True)
        import shutil
        shutil.copy(db, ab_dir / "AddressBook-v22.abcddb")
        result = _number_in_contacts("+919876543210")
    assert result is True


def test_number_in_contacts_not_found(tmp_path):
    db = _make_addressbook_db(["+91 11111 11111"])
    with patch("hooks.gates.Path.home") as mock_home:
        mock_home.return_value = tmp_path
        ab_dir = tmp_path / "Library/Application Support/AddressBook/Sources/fake-source"
        ab_dir.mkdir(parents=True)
        import shutil
        shutil.copy(db, ab_dir / "AddressBook-v22.abcddb")
        result = _number_in_contacts("+919876543210")
    assert result is False


def test_number_in_contacts_too_short_returns_false():
    assert _number_in_contacts("12345") is False


def test_number_in_contacts_no_db_returns_false(tmp_path):
    with patch("hooks.gates.Path.home", return_value=tmp_path):
        result = _number_in_contacts("+919876543210")
    assert result is False
