"""Send-gate policy — lookup-before-send enforcement.

Single source of truth for which tools are gated and what prerequisites they
require. Completely independent of DB state — operates purely on GateContext (in-memory dataclass).

Adding a gate for an external MCP tool: edit ~/.claude/gate_rules.yaml — no Python change needed.
Adding a gate with custom DB logic: add a Gate subclass below + register in GATES.

Anti-hallucination principle: Claude cannot be trusted to remember whether it
already verified something. Only tool call records in prompt_tool_calls (written
by the hook infrastructure, not the model) are facts. Gates enforce this.

External tool gate rules live in ~/.claude/gate_rules.yaml (or CLAUDE_GATE_RULES env var).
They are loaded at module import time and registered into GATES automatically.
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
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


def prereq(
    tool: str,
    window_s: float = DEFAULT_WINDOW_S,
    name_arg: str = "",
    input_arg: str = "",
) -> Callable[[type], type]:
    """Class decorator that injects a time-bounded prereq check as verify().

    Args:
        tool:      The prerequisite tool that must have been called recently.
        window_s:  How many seconds back to look (default: DEFAULT_WINDOW_S).
        name_arg:  Key in the PREREQ tool's input that must be non-empty, and whose
                   value must appear in the current or previous prompt text. Use for
                   lookup prereqs (e.g. contacts__search name="Alice").
        input_arg: Key in the GATED tool's own input whose value must appear in the
                   current or previous prompt text. Use when the relevant value is on
                   the gated call itself (e.g. mail__compose to="alice@example.com").

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

            # Check input_arg presence in prompt before scanning prereq history —
            # fail fast if the gated tool's own input isn't in the prompt.
            if input_arg:
                value = (ctx.tool_input.get(input_arg) or "").lower().strip()
                if value:
                    value_found = any(
                        value in pt.lower()
                        for pt in ctx.prompt_texts()
                        if pt
                    )
                    _log.info("[%s] input_arg_check %s=%r found_in_recent=%s",
                              gated, input_arg, value, value_found)
                    if not value_found:
                        reason = (
                            f"Blocked: {gated} — '{ctx.tool_input.get(input_arg)}' "
                            f"does not appear in the current or previous prompt. "
                            f"Confirm the intended value first."
                        )
                        tag = f"[{gated}] prompt={ctx.prompt_id[:8] if ctx.prompt_id else '?'}"
                        _log.warning("%s DENY reason=%s", tag, reason.split(".")[0])
                        return True, reason

            for tc in ctx.prev_tools():
                if tc.tool != tool:
                    continue
                if name_arg and not tc.tool_input.get(name_arg):
                    continue
                if tc.ts < cutoff:
                    continue
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
                        reason = (
                            f"Blocked: {gated} — {tool} was called for "
                            f"'{tc.tool_input.get(name_arg)}' but that name does not appear "
                            f"in the current or previous prompt. Search for the intended recipient first."
                        )
                        tag = f"[{gated}] prompt={ctx.prompt_id[:8] if ctx.prompt_id else '?'}"
                        _log.warning("%s DENY reason=%s", tag, reason.split(".")[0])
                        return True, reason
                tag = f"[{gated}] prompt={ctx.prompt_id[:8] if ctx.prompt_id else '?'}"
                _log.info("%s ALLOW prereq=%s", tag, tool)
                return False, ""

            qualifier = f" with a non-empty '{name_arg}' arg" if name_arg else ""
            deny_reason = (
                f"Blocked: {gated} requires {tool}{qualifier} within the last "
                f"{int(window_s)}s. Call {tool} first, then retry."
            )
            tag = f"[{gated}] prompt={ctx.prompt_id[:8] if ctx.prompt_id else '?'}"
            _log.warning("%s DENY reason=%s", tag, deny_reason.split(".")[0])
            return True, deny_reason

        cls.verify = verify
        cls.__abstractmethods__ = cls.__abstractmethods__ - {"verify"}
        return cls

    return _decorator


# ---------------------------------------------------------------------------
# External gate loader — reads ~/.claude/gate_rules.yaml (or CLAUDE_GATE_RULES)
# ---------------------------------------------------------------------------

_GATE_RULES_DEFAULT = Path.home() / ".claude" / "gate_rules.yaml"


def _load_external_gates(path: Path | None = None) -> dict[str, Gate]:
    """Load prereq-style gates from a YAML config file.

    Returns a dict of {tool_name: Gate} ready to merge into GATES.
    Fails open on any error — a missing or malformed config never blocks tools.
    """
    rules_path = path or Path(os.environ.get("CLAUDE_GATE_RULES", str(_GATE_RULES_DEFAULT)))
    if not rules_path.exists():
        _log.debug("[gates] gate_rules not found at %s — skipping external gates", rules_path)
        return {}

    try:
        import yaml  # pyyaml — available in project deps
        with rules_path.open() as f:
            config = yaml.safe_load(f) or {}
    except Exception as exc:
        _log.warning("[gates] failed to load %s: %s — no external gates registered", rules_path, exc)
        return {}

    loaded: dict[str, Gate] = {}
    for entry in config.get("gates", []):
        tool_name = (entry.get("tool") or "").strip()
        prereq_tool = (entry.get("prereq") or "").strip()
        if not tool_name or not prereq_tool:
            _log.warning("[gates] skipping malformed entry (missing tool/prereq): %s", entry)
            continue

        window_s = float(entry.get("window_s", DEFAULT_WINDOW_S))
        name_arg = (entry.get("name_arg") or "").strip()
        input_arg = (entry.get("input_arg") or "").strip()

        # Dynamically build a Gate subclass using the prereq() decorator
        cls = type(f"_ExternalGate_{tool_name}", (Gate,), {"tool_name": tool_name})
        cls = prereq(prereq_tool, window_s=window_s, name_arg=name_arg, input_arg=input_arg)(cls)
        loaded[tool_name] = cls()
        _log.info("[gates] registered external gate: %s → prereq=%s window=%ss", tool_name, prereq_tool, int(window_s))

    return loaded


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
            _log.debug("[Bash] non-commit bash — allow")
            return False, ""
        if _TASK_ID_RE.search(command):
            _log.info("[Bash] git commit with task:<id> — allow")
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
        _log.info("[git__commit] task_id=%s — allow", task_id)
        return False, ""


# ---------------------------------------------------------------------------
# Jira hierarchy gate
# ---------------------------------------------------------------------------

_JIRA_RULES: dict[str, set[str]] = {
    "story":    {"epic"},
    "task":     {"epic"},
    "bug":      {"epic"},
    "subtask":  {"story", "task", "bug"},
    "feedback": {"epic", "story", "task", "bug", "subtask"},
}


def validate_jira_hierarchy(issue_type: str, parent_id: str) -> str | None:
    """Pure validation of Jira parent-child type rules.

    Returns an error string if invalid, None if valid.
    Callable from both JiraHierarchyGate and the UI route — single source of truth.
    """
    issue_type = (issue_type or "task").lower()
    parent_id  = (parent_id or "").strip()

    if issue_type == "epic":
        if parent_id:
            return "Epics cannot have a parent. Remove parent_id or change issue_type."
        return None

    required_parents = _JIRA_RULES.get(issue_type)
    if required_parents is None:
        return None  # unknown type — pass through

    if not parent_id:
        return (
            f"issue_type='{issue_type}' requires a parent "
            f"(must be: {', '.join(sorted(required_parents))}). "
            f"Select a parent or change the type."
        )

    try:
        from src.tools.tasks import _connect
        with _connect() as conn:
            row = conn.execute(
                "SELECT issue_type FROM open_tasks WHERE id=?", (parent_id,)
            ).fetchone()
    except Exception as exc:
        _log.warning("[validate_jira_hierarchy] DB lookup failed: %s — failing open", exc)
        return None

    if row is None:
        _log.warning("[validate_jira_hierarchy] parent_id=%s not found", parent_id)
        return f"Parent task '{parent_id}' not found."

    parent_type = (row["issue_type"] or "task").lower()
    _log.info("[validate_jira_hierarchy] issue_type=%s parent_id=%s parent_type=%s required=%s",
              issue_type, parent_id, parent_type, sorted(required_parents))
    if parent_type not in required_parents:
        return (
            f"issue_type='{issue_type}' requires a parent of type "
            f"{', '.join(sorted(required_parents))}, "
            f"but '{parent_id}' is a '{parent_type}'."
        )

    return None


class JiraHierarchyGate(Gate):
    """Gate for tasks__create — enforces Jira parent-child issue type rules.

    story / task / bug  → parent must be an epic
    subtask             → parent must be a story, task, or bug
    epic                → no parent allowed
    Delegates to validate_jira_hierarchy() — no logic lives here.
    """
    tool_name = "tasks__create"

    def verify(self, ctx: GateContext) -> tuple[bool, str]:
        error = validate_jira_hierarchy(
            ctx.tool_input.get("issue_type", ""),
            ctx.tool_input.get("parent_id", ""),
        )
        if error:
            return True, f"Blocked: {error}"
        return False, ""


# ---------------------------------------------------------------------------
# Task lifecycle — shared state machine helper + per-tool gates
# ---------------------------------------------------------------------------

def _check_task_transition(task_id: str, to_status: str, body: str = "") -> tuple[bool, str]:
    """Enforce the task state machine for any lifecycle transition.

    Returns (deny, reason). Fails open on DB errors or missing task.
    """
    try:
        from src.tools.tasks import _connect, is_valid_transition
        with _connect() as conn:
            row = conn.execute(
                "SELECT status FROM open_tasks WHERE id=?", (task_id,)
            ).fetchone()
    except Exception as exc:
        _log.warning("[_check_task_transition] DB lookup failed: %s — failing open", exc)
        return False, ""

    if row is None:
        return False, ""

    current_status = (row["status"] or "").lower()

    if not is_valid_transition(current_status, to_status):
        from src.tools.tasks import _TRANSITIONS
        allowed = sorted(_TRANSITIONS.get(current_status, set()) | {"abandoned"})
        return (
            True,
            f"Blocked: task '{task_id}' cannot transition from '{current_status}' to '{to_status}'. "
            f"Allowed next states: {', '.join(allowed)}.",
        )

    _log.info("[_check_task_transition] task=%s %s→%s allow", task_id, current_status, to_status)
    return False, ""


class TaskSetActiveGate(Gate):
    """Gate for tasks__set_active — task must exist and be in an activatable state (open or blocked).

    'active' is checkpoint-only and never written to the DB, so _check_task_transition
    cannot be used here. We just verify the task is in a workable state.
    """
    tool_name = "tasks__set_active"

    def verify(self, ctx: GateContext) -> tuple[bool, str]:
        task_id = (ctx.tool_input.get("task_id") or "").strip()
        if not task_id:
            return False, ""
        try:
            from src.tools.tasks import _connect
            with _connect() as conn:
                row = conn.execute(
                    "SELECT status FROM open_tasks WHERE id = ?", (task_id,)
                ).fetchone()
        except Exception as exc:
            _log.warning("[TaskSetActiveGate] DB lookup failed: %s — failing open", exc)
            return False, ""
        if row is None:
            return True, f"Blocked: task '{task_id}' not found."
        if row["status"] not in ("open", "blocked", "wip"):
            return True, (
                f"Blocked: task '{task_id}' has status '{row['status']}' and cannot be activated. "
                f"Only open, blocked, or wip tasks can be made active."
            )
        return False, ""


class TaskUpdateGate(Gate):
    """Gate for tasks__update — state machine check on status changes."""
    tool_name = "tasks__update"

    def verify(self, ctx: GateContext) -> tuple[bool, str]:
        new_status = (ctx.tool_input.get("status") or "").strip().lower()
        task_id = (ctx.tool_input.get("id") or "").strip()

        if not new_status:
            return False, ""
        if not task_id:
            return False, ""

        return _check_task_transition(task_id, new_status, ctx.tool_input.get("body") or "")


class TaskFinishGate(Gate):
    """Gate for tasks__finish — enforces active→done via _check_task_transition."""
    tool_name = "tasks__finish"

    def verify(self, ctx: GateContext) -> tuple[bool, str]:
        task_id = (ctx.tool_input.get("task_id") or "").strip()
        if not task_id:
            return False, ""
        return _check_task_transition(task_id, "done", ctx.tool_input.get("reason") or "")


# ---------------------------------------------------------------------------
# Gate registry
# ---------------------------------------------------------------------------

GATES: dict[str, Gate] = {g.tool_name: g for g in [
    GitCommitGate(),
    GitCommitMcpGate(),
    JiraHierarchyGate(),
    TaskSetActiveGate(),
    TaskUpdateGate(),
    TaskFinishGate(),
]}

# Merge external gates from gate_rules.yaml — external entries never override internal ones
GATES = {**_load_external_gates(), **GATES}


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


