"""Send-gate policy — lookup-before-send enforcement.

Single source of truth for which tools are gated and what prerequisites they
require. Completely independent of sessions.db, hooks, and LangChain.

Adding a new gate = one new Gate(...) entry in _GATES. Nothing else changes.

Anti-hallucination principle: Claude cannot be trusted to remember whether it
already verified something. Only tool call records in prompt_tool_calls (written
by the hook infrastructure, not the model) are facts. Gates enforce this.

Confirmation strategy for irreversible tools (e.g. imessage__send):
  - This gate enforces contacts__search ran first (anti-hallucination on number).
  - The actual user confirmation comes from the Claude Code native permission
    dialog — the UX click that fires when a tool is NOT in the settings.json
    allow list. That dialog is the canonical confirmation gate; confirm__send
    is NOT required as a prereq because it creates a cross-turn timing problem
    (prompt_tools resets on each UserPromptSubmit, so confirm__send called in
    turn N is invisible to the gate in turn N+1).
  - To ensure the dialog fires: keep mcp__local-mac__imessage__send out of
    the allow list in .claude/settings.json.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal


@dataclass(frozen=True)
class Gate:
    """Declarative prerequisite rule for an irreversible tool call.

    Args:
        tool_name:  short tool name (e.g. "imessage__send") — no mcp__ prefix.
        prereqs:    list of short tool names that must have run first.
        logic:      "any" (default) — at least one prereq; "all" — every prereq.
        message:    optional custom deny message; auto-generated if empty.
    """
    tool_name: str
    prereqs: list[str]
    logic: Literal["any", "all"] = "any"
    message: str = ""

    def is_satisfied(self, prompt_had: Callable[[str], bool]) -> bool:
        """Return True if prerequisites are satisfied under the current prompt.

        prompt_had must be a function that returns True only when the named
        tool was actually called (DB record) — not model recall.
        """
        checks = [prompt_had(p) for p in self.prereqs]
        return all(checks) if self.logic == "all" else any(checks)

    def deny_reason(self) -> str:
        if self.message:
            return self.message
        prereq_list = " or ".join(self.prereqs)
        return (
            f"Blocked: {self.tool_name} requires {prereq_list} first. "
            f"Look up the recipient, show the name + number, "
            f"get explicit confirmation, then proceed. Never act on a guessed or "
            f"recalled value — it can reach the wrong person."
        )


# ---------------------------------------------------------------------------
# Gate registry — add new gates here
# ---------------------------------------------------------------------------

GATES: dict[str, Gate] = {g.tool_name: g for g in [
    Gate(
        tool_name="imessage__send",
        prereqs=["contacts__search", "confirm__send"],
        logic="all",
        message=(
            "Blocked: imessage__send requires contacts__search AND confirm__send first. "
            "Look up the recipient with contacts__search, show the name + number, "
            "ask the user to confirm, call confirm__send, then send. "
            "confirm__send may have been called in the previous prompt turn — the gate checks both "
            "the current and previous prompt's tool history via session_tools. "
            "Never send to a guessed or recalled number — it can reach the wrong person."
        ),
    ),
    Gate(
        tool_name="mail__compose",
        prereqs=["contacts__search"],
        message=(
            "Blocked: mail__compose requires contacts__search first. "
            "Look up the recipient with contacts__search, confirm the address, "
            "then compose. Never send to a guessed or recalled address."
        ),
    ),
]}


_AB_GLOB = Path.home() / "Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb"
_DIGITS_RE = re.compile(r"^\+?[\d\s\-().]{7,}$")


def _is_phone_number(value: str) -> bool:
    digits = re.sub(r"\D", "", value)
    return 10 <= len(digits) <= 12


def _number_in_contacts(number: str) -> bool:
    """Return True if number matches any record in the system AddressBook."""
    digits = re.sub(r"\D", "", number)
    if not (10 <= len(digits) <= 12):
        return False
    for db_path in Path.home().glob("Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb"):
        try:
            with sqlite3.connect(str(db_path)) as con:
                row = con.execute(
                    "SELECT 1 FROM ZABCDPHONENUMBER WHERE replace(replace(replace(replace(ZFULLNUMBER,' ',''),'-',''),'(',''),')','') LIKE ? LIMIT 1",
                    (f"%{digits}%",),
                ).fetchone()
                if row:
                    return True
        except Exception:
            continue
    return False


def check(tool_short_name: str, prompt_had: Callable[[str], bool], tool_input: dict | None = None) -> tuple[bool, str]:
    """Check whether tool_short_name is gated and if so whether the gate is satisfied.

    Returns (deny, reason):
        deny=False  → tool is allowed (not gated, or gate satisfied)
        deny=True   → tool must be blocked; reason is the message for Claude
    """
    gate = GATES.get(tool_short_name)
    if gate is None:
        return False, ""
    if not gate.is_satisfied(prompt_had):
        return True, gate.deny_reason()

    # Secondary check: recipient must be a phone number in contacts.
    if tool_short_name == "imessage__send" and tool_input:
        to = (tool_input.get("recipient") or "").strip()
        if to and not _is_phone_number(to):
            return True, (
                f"Blocked: {to!r} is not a valid phone number. "
                "Use the number returned by contacts__search, not a name or guessed value."
            )
        if to and _is_phone_number(to) and not _number_in_contacts(to):
            return True, (
                f"Blocked: the number {to!r} is not in your contacts. "
                "Only send messages to known contacts. Verify the recipient first."
            )

    return False, ""
