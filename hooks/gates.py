"""Send-gate policy — lookup-before-send enforcement.

Single source of truth for which tools are gated and what prerequisites they
require. Completely independent of sessions.db, hooks, and LangChain.

Adding a new gate = one new Gate(...) entry in _GATES. Nothing else changes.

Anti-hallucination principle: Claude cannot be trusted to remember whether it
already verified something. Only tool call records in prompt_tool_calls (written
by the hook infrastructure, not the model) are facts. Gates enforce this.
"""
from __future__ import annotations

from dataclasses import dataclass, field
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
        prereqs=["contacts__search"],
        message=(
            "Blocked: imessage__send requires contacts__search first. "
            "Look up the recipient with contacts__search, show the name + number, "
            "get explicit confirmation, then send. Never send to a guessed or "
            "recalled number — it can reach the wrong person."
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


def check(tool_short_name: str, prompt_had: Callable[[str], bool]) -> tuple[bool, str]:
    """Check whether tool_short_name is gated and if so whether the gate is satisfied.

    Returns (deny, reason):
        deny=False  → tool is allowed (not gated, or gate satisfied)
        deny=True   → tool must be blocked; reason is the message for Claude
    """
    gate = GATES.get(tool_short_name)
    if gate is None:
        return False, ""
    if gate.is_satisfied(prompt_had):
        return False, ""
    return True, gate.deny_reason()
