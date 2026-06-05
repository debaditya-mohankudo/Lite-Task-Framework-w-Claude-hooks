"""Send-gate policy — lookup-before-send enforcement.

Single source of truth for which tools are gated and what prerequisites they
require. Completely independent of sessions.db, hooks, and LangChain.

Adding a new gate = one new Gate subclass + one entry in GATES. Nothing else changes.

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

from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass, field

from src.logger import get_logger

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# GateContext — prepared once from SessionState, passed to every gate
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    tool: str
    prompt_id: str
    tool_input: dict = field(default_factory=dict)
    tool_result: dict = field(default_factory=dict)
    found: bool = False


@dataclass
class GateContext:
    """Prepared view of session state passed to every gate's verify().

    Built once in gate_check.py from SessionState; each gate uses what it needs.
    """
    tool_name: str
    tool_input: dict

    # Rich call records from prompt_tools (current prompt only)
    current_calls: list[ToolCall]

    # Tool names only from session history (all prompts, keyed by prompt_id)
    session_tools: OrderedDict[str, list[str]]

    # Ordered prompt ids this session
    session_prompt_ids: list[str]

    # Current prompt id
    prompt_id: str

    def called_this_prompt(self, tool: str) -> bool:
        return any(c.tool == tool for c in self.current_calls)

    def called_prev_prompt(self, tool: str) -> bool:
        prev_id = self._prev_prompt_id()
        if not prev_id:
            return False
        return tool in self.session_tools.get(prev_id, [])

    def called_this_session(self, tool: str) -> bool:
        return any(
            tool in tools
            for tools in self.session_tools.values()
        )

    def result_for(self, tool: str) -> dict | None:
        """Return tool_result from the most recent call to tool this prompt."""
        for c in reversed(self.current_calls):
            if c.tool == tool:
                return c.tool_result
        return None

    def _prev_prompt_id(self) -> str | None:
        if self.prompt_id in self.session_prompt_ids:
            idx = self.session_prompt_ids.index(self.prompt_id)
            if idx > 0:
                return self.session_prompt_ids[idx - 1]
        return None


# ---------------------------------------------------------------------------
# Base Gate ABC
# ---------------------------------------------------------------------------

class Gate(ABC):
    """Abstract base for all gate types.

    Each subclass encapsulates its own verification logic — prereq checks,
    input validation, state checks — and owns its deny message.

    Subclasses implement verify(ctx) -> tuple[bool, str]:
        (True, reason)  → deny the tool call
        (False, "")     → allow

    Logging is handled automatically: the base class wraps verify() at
    instantiation time so subclasses never need to import or call _log.
    """

    @property
    @abstractmethod
    def tool_name(self) -> str:
        """Short tool name this gate applies to (no mcp__ prefix)."""

    @abstractmethod
    def verify(self, ctx: GateContext) -> tuple[bool, str]:
        """Return (deny, reason). deny=True blocks the tool call."""

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        _original = cls.__dict__.get("verify")
        if _original is None:
            return

        def _logged_verify(self: Gate, ctx: GateContext) -> tuple[bool, str]:
            tag = f"[{self.tool_name}] prompt={ctx.prompt_id[:8] if ctx.prompt_id else '?'}"
            deny, reason = _original(self, ctx)
            if deny:
                _log.warning("%s DENY reason=%s", tag, reason.split(".")[0])
            else:
                _log.info("%s ALLOW", tag)
            return deny, reason

        cls.verify = _logged_verify


# ---------------------------------------------------------------------------
# Concrete gate classes
# ---------------------------------------------------------------------------

class IMessageSendGate(Gate):
    """Gate for imessage__send.

    Checks:
      1. contacts__search was called this session (anti-hallucination on number)
      2. contacts__search returned at least one result (contact must exist)
      3. confirm__send was called this or the previous prompt (cross-turn UX confirmation)
    """

    tool_name = "imessage__send"

    def verify(self, ctx: GateContext) -> tuple[bool, str]:
        if not ctx.called_this_session("contacts__search"):
            return True, (
                "Blocked: imessage__send requires contacts__search first. "
                "Look up the recipient with contacts__search, show the name + number, "
                "ask the user to confirm, then send. "
                "Never send to a guessed or recalled number — it can reach the wrong person."
            )

        # Only inspect the result if contacts__search ran this prompt; if it ran
        # last prompt the result is no longer in current_calls.
        if ctx.called_this_prompt("contacts__search"):
            result = ctx.result_for("contacts__search")
            if _contacts_empty(result):
                return True, (
                    "Blocked: contacts__search returned no results for the recipient. "
                    "Only send to contacts that exist in the address book."
                )

        if not (ctx.called_this_prompt("confirm__send") or ctx.called_prev_prompt("confirm__send")):
            return True, (
                "Blocked: imessage__send requires confirm__send first. "
                "Call confirm__send to get explicit user confirmation, then send."
            )

        return False, ""


class MailComposeGate(Gate):
    """Gate for mail__compose.

    Checks:
      1. contacts__search was called this session (anti-hallucination on address)
    """

    tool_name = "mail__compose"

    def verify(self, ctx: GateContext) -> tuple[bool, str]:
        if not ctx.called_this_session("contacts__search"):
            return True, (
                "Blocked: mail__compose requires contacts__search first. "
                "Look up the recipient with contacts__search, confirm the address, "
                "then compose. Never send to a guessed or recalled address."
            )
        return False, ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _contacts_empty(result: dict | list | None) -> bool:
    """Return True if a contacts__search result contains no contacts."""
    if result is None:
        return True
    if isinstance(result, list):
        return len(result) == 0
    # MCP wraps results in {"content": [...]} or {"contacts": [...]}
    for key in ("contacts", "results", "content"):
        val = result.get(key)
        if isinstance(val, list):
            return len(val) == 0
    return False


# ---------------------------------------------------------------------------
# Gate registry
# ---------------------------------------------------------------------------

GATES: dict[str, Gate] = {g.tool_name: g for g in [
    IMessageSendGate(),
    MailComposeGate(),
]}


def check(tool_short_name: str, ctx: GateContext) -> tuple[bool, str]:
    """Dispatch to the gate for tool_short_name, if one exists.

    Returns (deny, reason):
        deny=False  → tool is allowed (not gated, or gate satisfied)
        deny=True   → tool must be blocked; reason is the message for Claude
    """
    gate = GATES.get(tool_short_name)
    if gate is None:
        _log.debug("[gates.check] tool=%s not_gated → allow", tool_short_name)
        return False, ""
    return gate.verify(ctx)


