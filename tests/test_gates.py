"""Tests for hooks/gates.py — Gate dataclass, registry, and check()."""
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from hooks.gates import Gate, GATES, check, _is_phone_number, _number_in_contacts


# ---------------------------------------------------------------------------
# Gate.is_satisfied
# ---------------------------------------------------------------------------

def test_gate_any_logic_satisfied_when_one_prereq_met():
    gate = Gate(tool_name="foo", prereqs=["a", "b"], logic="any")
    assert gate.is_satisfied(lambda t: t == "a")


def test_gate_any_logic_denied_when_no_prereqs_met():
    gate = Gate(tool_name="foo", prereqs=["a", "b"], logic="any")
    assert not gate.is_satisfied(lambda t: False)


def test_gate_all_logic_satisfied_when_all_prereqs_met():
    gate = Gate(tool_name="foo", prereqs=["a", "b"], logic="all")
    assert gate.is_satisfied(lambda t: True)


def test_gate_all_logic_denied_when_one_prereq_missing():
    gate = Gate(tool_name="foo", prereqs=["a", "b"], logic="all")
    assert not gate.is_satisfied(lambda t: t == "a")


# ---------------------------------------------------------------------------
# Gate.deny_reason
# ---------------------------------------------------------------------------

def test_deny_reason_uses_custom_message():
    gate = Gate(tool_name="foo", prereqs=["a"], message="Custom block message")
    assert gate.deny_reason() == "Custom block message"


def test_deny_reason_auto_generated_when_no_message():
    gate = Gate(tool_name="foo__send", prereqs=["contacts__search"])
    reason = gate.deny_reason()
    assert "foo__send" in reason
    assert "contacts__search" in reason


def test_deny_reason_auto_generated_multiple_prereqs():
    gate = Gate(tool_name="foo", prereqs=["a", "b"])
    reason = gate.deny_reason()
    assert "a or b" in reason


# ---------------------------------------------------------------------------
# GATES registry
# ---------------------------------------------------------------------------

def test_imessage_send_gate_exists():
    assert "imessage__send" in GATES


def test_mail_compose_gate_exists():
    assert "mail__compose" in GATES


def test_imessage_send_requires_contacts_search():
    gate = GATES["imessage__send"]
    assert "contacts__search" in gate.prereqs


def test_mail_compose_requires_contacts_search():
    gate = GATES["mail__compose"]
    assert "contacts__search" in gate.prereqs


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
# check() — ungated tool
# ---------------------------------------------------------------------------

def test_check_ungated_tool_always_allowed():
    deny, reason = check("some__unknown_tool", lambda t: False)
    assert deny is False
    assert reason == ""


# ---------------------------------------------------------------------------
# check() — gate not satisfied (no prereq)
# ---------------------------------------------------------------------------

def test_check_imessage_denied_without_contacts_search():
    deny, reason = check("imessage__send", lambda t: False)
    assert deny is True
    assert "contacts__search" in reason


def test_check_mail_compose_denied_without_contacts_search():
    deny, reason = check("mail__compose", lambda t: False)
    assert deny is True
    assert "contacts__search" in reason


# ---------------------------------------------------------------------------
# check() — gate satisfied
# ---------------------------------------------------------------------------

def test_check_imessage_allowed_after_contacts_search():
    deny, _ = check("imessage__send", lambda t: t == "contacts__search")
    assert deny is False


def test_check_mail_compose_allowed_after_contacts_search():
    deny, _ = check("mail__compose", lambda t: t == "contacts__search")
    assert deny is False


# ---------------------------------------------------------------------------
# check() — secondary phone number check
# ---------------------------------------------------------------------------

_CONTACTS_HAD = lambda t: t == "contacts__search"


def test_check_imessage_allowed_when_to_is_name():
    deny, _ = check(
        "imessage__send",
        _CONTACTS_HAD,
        tool_input={"to": "John Doe"},
    )
    assert deny is False


def test_check_imessage_denied_when_number_not_in_contacts():
    with patch("hooks.gates._number_in_contacts", return_value=False):
        deny, reason = check(
            "imessage__send",
            _CONTACTS_HAD,
            tool_input={"recipient": "+919876543210"},
        )
    assert deny is True
    assert "not in your contacts" in reason


def test_check_imessage_allowed_when_number_in_contacts():
    with patch("hooks.gates._number_in_contacts", return_value=True):
        deny, _ = check(
            "imessage__send",
            _CONTACTS_HAD,
            tool_input={"recipient": "+919876543210"},
        )
    assert deny is False


def test_check_imessage_no_tool_input_skips_secondary():
    deny, _ = check("imessage__send", _CONTACTS_HAD, tool_input=None)
    assert deny is False


def test_check_imessage_empty_to_skips_secondary():
    deny, _ = check(
        "imessage__send",
        _CONTACTS_HAD,
        tool_input={"to": ""},
    )
    assert deny is False


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
        # Place the fake db at the expected glob path
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
