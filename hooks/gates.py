"""Send-gate policy — lookup-before-send enforcement.

Single source of truth for which tools are gated and what prerequisites they
require. Completely independent of DB state — operates purely on GateContext (in-memory dataclass).

Adding a new gate = one new Gate subclass + one entry in GATES. Nothing else changes.

Anti-hallucination principle: Claude cannot be trusted to remember whether it
already verified something. Only tool call records in prompt_tool_calls (written
by the hook infrastructure, not the model) are facts. Gates enforce this.

Confirmation strategy for irreversible tools (e.g. imessage__send):
  - contacts__search must have run recently with a non-empty name arg.
  - The searched name must appear as a substring in the current prompt text —
    preventing a stale or hallucinated lookup from satisfying the gate.
  - User confirmation comes from the Claude Code native permission dialog —
    keep mcp__local-mac__imessage__send out of the allow list in settings.json.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable

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
    ts: float = 0.0


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

    # Raw prompt text for name presence checks (lower-cased)
    prompt_text: str = ""

    # Current + previous prompt texts (current first); used for multi-turn name checks
    recent_prompt_texts: list[str] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.recent_prompt_texts is None:
            self.recent_prompt_texts = [self.prompt_text] if self.prompt_text else []

    def prompt_texts(self):
        """Yield recent prompt texts, current first."""
        yield from self.recent_prompt_texts

    def called_this_session(self, tool: str) -> bool:
        return any(
            (entry.get("tool") if isinstance(entry, dict) else entry if isinstance(entry, str) else None) == tool
            for bucket in self.session_tools.values()
            for entry in bucket
        )

    def called_recently(self, tool: str, window_s: float = 120.0) -> bool:
        """Return True if tool was called within window_s seconds."""
        import time
        cutoff = time.time() - window_s
        for tc in self.prev_tools():
            if tc.tool == tool and tc.ts >= cutoff:
                return True
        return False

    def prev_tools(self):
        """Yield ToolCall objects in reverse call order (most recent first)."""
        history: list[ToolCall] = []
        for bucket in self.session_tools.values():
            for entry in bucket:
                if isinstance(entry, dict) and "tool" in entry:
                    history.append(ToolCall(
                        tool=entry["tool"],
                        prompt_id="",
                        tool_input=entry.get("tool_input", {}),
                        ts=entry.get("ts", 0.0),
                    ))
                elif isinstance(entry, str):
                    history.append(ToolCall(tool=entry, prompt_id=""))
        history.extend(self.current_calls)
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

DEFAULT_WINDOW_S = 120.0  # seconds — default staleness window for all prereq checks


def prereq(tool: str, window_s: float = DEFAULT_WINDOW_S, name_arg: str = "") -> Callable[[type], type]:
    """Class decorator that injects a time-bounded prereq check as verify().

    Args:
        tool:     The prerequisite tool that must have been called recently.
        window_s: How many seconds back to look (default: DEFAULT_WINDOW_S).
        name_arg: If set, two checks apply:
                  1. The prereq tool_input must contain this key with a non-empty value.
                  2. That value must appear as a substring in the current or previous prompt text
                     (case-insensitive), preventing a stale hallucinated lookup from satisfying the gate.
                  Check 2 always runs — denies if the name is absent from all available prompt texts.

    Usage:
        @prereq("contacts__search", window_s=120, name_arg="name")
        class IMessageSendGate(Gate):
            tool_name = "imessage__send"
    """
    def _decorator(cls: type) -> type:
        gated = cls.tool_name if hasattr(cls, "tool_name") else cls.__name__

        def verify(_self: Gate, ctx: GateContext) -> tuple[bool, str]:
            import time
            cutoff = time.time() - window_s
            for tc in ctx.prev_tools():
                if tc.tool != tool:
                    continue
                if name_arg and not tc.tool_input.get(name_arg):
                    continue
                if tc.ts < cutoff:
                    continue
                # If name_arg is set, verify the searched name appears in current or previous prompt
                if name_arg:
                    searched_name = tc.tool_input.get(name_arg, "").lower()
                    name_found = any(
                        searched_name in pt.lower()
                        for pt in ctx.prompt_texts()
                        if pt
                    )
                    _log.info("[%s] name_arg_check name=%r found_in_recent=%s",
                              gated, searched_name, name_found)
                    if searched_name and not name_found:
                        deny, reason = True, (
                            f"Blocked: {gated} — contacts__search was called for "
                            f"'{tc.tool_input.get(name_arg)}' but that name does not appear "
                            f"in the current or previous prompt. Search for the intended recipient first."
                        )
                        break
                deny, reason = False, ""
                break
            else:
                qualifier = f" with a non-empty '{name_arg}' arg" if name_arg else ""
                deny, reason = True, (
                    f"Blocked: {gated} requires {tool}{qualifier} within the last "
                    f"{int(window_s)}s. Call {tool} first, then retry."
                )
            tag = f"[{gated}] prompt={ctx.prompt_id[:8] if ctx.prompt_id else '?'}"
            if deny:
                _log.warning("%s DENY reason=%s", tag, reason.split(".")[0])
            else:
                _log.info("%s ALLOW prereq=%s", tag, tool)
            return deny, reason

        cls.verify = verify
        cls.__abstractmethods__ = cls.__abstractmethods__ - {"verify"}
        return cls

    return _decorator


@prereq("contacts__search", window_s=DEFAULT_WINDOW_S, name_arg="name")
class IMessageSendGate(Gate):
    """Gate for imessage__send — requires contacts__search with name arg within window.

    Also verifies the searched name appears in the current prompt text to prevent
    a stale or hallucinated contact lookup from satisfying the gate.
    """
    tool_name = "imessage__send"


@prereq("contacts__search", window_s=DEFAULT_WINDOW_S)
class MailComposeGate(Gate):
    """Gate for mail__compose — requires contacts__search within window."""
    tool_name = "mail__compose"


@prereq("mail__read", window_s=DEFAULT_WINDOW_S)
class MailDeleteGate(Gate):
    """Gate for mail__delete — requires mail__read within window."""
    tool_name = "mail__delete"


import re as _re

_GIT_COMMIT_RE = _re.compile(
    r'git\s+(?:(?!commit\b)\S+\s+)*commit\b|git_local\.sh',
    _re.IGNORECASE,
)
_TASK_ID_RE = _re.compile(r'task:[a-f0-9]{6,}')


class GitCommitGate(Gate):
    """Gate for Bash tool calls that contain a git commit.

    Passes through all non-commit bash calls immediately. For commit calls,
    denies if no task:<id> pattern is found anywhere in the command string.
    This enforces traceability — every commit must reference an active task.
    """
    tool_name = "Bash"

    def verify(self, ctx: GateContext) -> tuple[bool, str]:
        command: str = ctx.tool_input.get("command", "")
        if not _GIT_COMMIT_RE.search(command):
            return False, ""
        if _TASK_ID_RE.search(command):
            return False, ""
        return (
            True,
            "Blocked: git commit is missing a task:<id> reference. "
            "Add 'task:<id>' to the commit message body, or activate a task first with tasks__set_active.",
        )


class GitCommitMcpGate(Gate):
    """Gate for git__commit MCP tool — requires non-empty task_id param.

    Cleaner than the Bash regex gate: task_id is a typed param so it
    can never be silently omitted or mangled by shell quoting.
    """
    tool_name = "git__commit"

    def verify(self, ctx: GateContext) -> tuple[bool, str]:
        task_id = (ctx.tool_input.get("task_id") or "").strip()
        if not task_id:
            return (
                True,
                "Blocked: git__commit requires a non-empty task_id for traceability. "
                "Pass the active task ID or activate a task first with tasks__set_active.",
            )
        return False, ""


# ---------------------------------------------------------------------------
# Jira hierarchy gate
# ---------------------------------------------------------------------------

_JIRA_RULES: dict[str, set[str]] = {
    "story":   {"epic"},
    "task":    {"epic"},
    "bug":     {"epic"},
    "subtask": {"story", "task", "bug"},
}


class JiraHierarchyGate(Gate):
    """Gate for tasks__create — enforces Jira parent-child issue type rules.

    story / task / bug  → parent must be an epic
    subtask             → parent must be a story, task, or bug
    epic                → no parent allowed
    Everything else (default task with no parent_id) → pass through.
    """
    tool_name = "tasks__create"

    def verify(self, ctx: GateContext) -> tuple[bool, str]:
        issue_type: str = (ctx.tool_input.get("issue_type") or "task").lower()
        parent_id: str = (ctx.tool_input.get("parent_id") or "").strip()

        if issue_type == "epic":
            if parent_id:
                return (
                    True,
                    "Blocked: epics cannot have a parent. Remove parent_id or change issue_type.",
                )
            return False, ""

        required_parents = _JIRA_RULES.get(issue_type)
        if required_parents is None:
            return False, ""  # unknown / default type — pass through

        if not parent_id:
            return (
                True,
                f"Blocked: issue_type='{issue_type}' requires a parent_id "
                f"(must be an {' or '.join(sorted(required_parents))}). "
                f"Create or reference the parent first.",
            )

        # Fetch parent's issue_type from DB
        try:
            from src.tools.tasks import _connect
            with _connect() as conn:
                row = conn.execute(
                    "SELECT issue_type FROM open_tasks WHERE id=?", (parent_id,)
                ).fetchone()
        except Exception as exc:
            _log.warning("[JiraHierarchyGate] DB lookup failed: %s — failing open", exc)
            return False, ""

        if row is None:
            return (
                True,
                f"Blocked: parent task '{parent_id}' not found. "
                f"Create the parent first.",
            )

        parent_type: str = (row["issue_type"] or "task").lower()
        if parent_type not in required_parents:
            return (
                True,
                f"Blocked: issue_type='{issue_type}' requires a parent of type "
                f"{' or '.join(sorted(required_parents))}, "
                f"but '{parent_id}' is a '{parent_type}'.",
            )

        return False, ""


# ---------------------------------------------------------------------------
# Gate registry
# ---------------------------------------------------------------------------

GATES: dict[str, Gate] = {g.tool_name: g for g in [
    IMessageSendGate(),
    MailComposeGate(),
    MailDeleteGate(),
    GitCommitGate(),
    GitCommitMcpGate(),
    JiraHierarchyGate(),
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


