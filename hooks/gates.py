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

    def called_this_session(self, tool: str) -> bool:
        return any(
            tool in tools
            for tools in self.session_tools.values()
        )

    def prev_tools(self):
        """Yield tool names in reverse call order (most recent first), excluding the current gated tool."""
        history: list[str] = []
        for tools in self.session_tools.values():
            history.extend(tools)
        history.extend(c.tool for c in self.current_calls)
        yield from reversed(history)



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

    tool_name: str

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
        prev = ctx.prev_tools()

        if next(prev, None) != "confirm__send":
            return True, (
                "Blocked: imessage__send requires confirm__send immediately before it. "
                "Call confirm__send to get explicit user confirmation, then send."
            )

        if next(prev, None) != "contacts__search":
            return True, (
                "Blocked: contacts__search must be called before confirm__send. "
                "Look up the recipient with contacts__search, show the name + number, "
                "ask the user to confirm, then send. "
                "Never send to a guessed or recalled number — it can reach the wrong person."
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


class MailDeleteGate(Gate):
    """Gate for mail__delete.

    Checks:
      1. mail__read was called immediately before this call (confirm user saw the mails)
    """

    tool_name = "mail__delete"

    def verify(self, ctx: GateContext) -> tuple[bool, str]:
        if next(ctx.prev_tools(), None) != "mail__read":
            return True, (
                "Blocked: mail__delete requires mail__read immediately before it. "
                "Read the mailbox with mail__read so the user can see what will be deleted, "
                "then delete."
            )
        return False, ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Gate registry
# ---------------------------------------------------------------------------

GATES: dict[str, Gate] = {g.tool_name: g for g in [
    IMessageSendGate(),
    MailComposeGate(),
    MailDeleteGate(),
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


