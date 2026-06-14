"""Tests for hooks/gates.py — Gate ABC, concrete gate classes, registry, and check()."""
import time
from collections import OrderedDict

import pytest

from hooks.gates import (
    Gate, GateContext, ToolCall, GATES, check,
    IMessageSendGate, MailComposeGate, MailDeleteGate, GitCommitGate,
    GitCommitMcpGate, JiraHierarchyGate,
    DEFAULT_WINDOW_S,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tc(tool: str, tool_input: dict | None = None, ts: float | None = None) -> dict:
    """Build a session_tools bucket entry. Defaults to a recent timestamp."""
    return {"tool": tool, "tool_input": tool_input or {}, "ts": ts if ts is not None else time.time()}


def _stale_ts() -> float:
    """Return a timestamp older than the staleness window."""
    return time.time() - DEFAULT_WINDOW_S - 10


def _ctx(
    tool_name: str = "imessage__send",
    tool_input: dict | None = None,
    current_tools: list[str] | None = None,
    session_tools: dict[str, list] | None = None,
    session_prompt_ids: list[str] | None = None,
    prompt_id: str = "p1",
    prompt_text: str = "",
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
        prompt_text=prompt_text,
    )


# ---------------------------------------------------------------------------
# Gate is ABC — cannot instantiate directly
# ---------------------------------------------------------------------------

def test_gate_is_abstract():
    with pytest.raises(TypeError):
        Gate()


# ---------------------------------------------------------------------------
# @prereq decorator — structural checks
# ---------------------------------------------------------------------------

def test_prereq_gates_are_instantiable():
    # Decorated gates must not remain abstract
    IMessageSendGate()
    MailComposeGate()
    MailDeleteGate()


def test_prereq_gates_preserve_tool_name():
    assert IMessageSendGate().tool_name == "imessage__send"
    assert MailComposeGate().tool_name == "mail__compose"
    assert MailDeleteGate().tool_name == "mail__delete"


def test_prereq_gates_are_gate_subclasses():
    assert isinstance(IMessageSendGate(), Gate)
    assert isinstance(MailComposeGate(), Gate)
    assert isinstance(MailDeleteGate(), Gate)


def test_prereq_gates_registered_in_registry():
    assert "imessage__send" in GATES
    assert "mail__compose" in GATES
    assert "mail__delete" in GATES


# ---------------------------------------------------------------------------
# GateContext.prev_tools — yields ToolCall objects
# ---------------------------------------------------------------------------

def test_ctx_prev_tools_yields_toolcall_objects():
    ctx = _ctx(
        session_tools={"p0": [_tc("contacts__search", {"name": "Alice"}), _tc("imessage__send")]},
        session_prompt_ids=["p0", "p1"],
        prompt_id="p1",
    )
    it = ctx.prev_tools()
    first = next(it)
    assert isinstance(first, ToolCall)
    assert first.tool == "imessage__send"
    second = next(it)
    assert second.tool == "contacts__search"
    assert second.tool_input == {"name": "Alice"}
    assert next(it, None) is None


def test_ctx_prev_tools_empty():
    ctx = _ctx(session_tools={}, session_prompt_ids=[], prompt_id="p1")
    assert next(ctx.prev_tools(), None) is None


# ---------------------------------------------------------------------------
# GateContext.called_this_session
# ---------------------------------------------------------------------------

def test_ctx_called_this_session():
    ctx = _ctx(
        session_tools={"p0": [_tc("contacts__search")]},
        session_prompt_ids=["p0", "p1"],
        prompt_id="p1",
    )
    assert ctx.called_this_session("contacts__search")
    assert not ctx.called_this_session("imessage__send")


# ---------------------------------------------------------------------------
# GateContext.called_recently
# ---------------------------------------------------------------------------

def test_ctx_called_recently_within_window():
    ctx = _ctx(
        session_tools={"p0": [_tc("contacts__search")]},
        session_prompt_ids=["p0", "p1"],
        prompt_id="p1",
    )
    assert ctx.called_recently("contacts__search", window_s=120.0)
    assert not ctx.called_recently("imessage__send", window_s=120.0)


def test_ctx_called_recently_stale():
    ctx = _ctx(
        session_tools={"p0": [_tc("contacts__search", ts=_stale_ts())]},
        session_prompt_ids=["p0", "p1"],
        prompt_id="p1",
    )
    assert not ctx.called_recently("contacts__search", window_s=120.0)


def test_ctx_called_recently_mixed_stale_and_fresh():
    # stale entry followed by a fresh one — should be allowed
    ctx = _ctx(
        session_tools={"p0": [
            _tc("contacts__search", ts=_stale_ts()),
            _tc("contacts__search"),
        ]},
        session_prompt_ids=["p0", "p1"],
        prompt_id="p1",
    )
    assert ctx.called_recently("contacts__search", window_s=120.0)


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
# IMessageSendGate — contacts__search within last 10 calls with name arg
# ---------------------------------------------------------------------------

def test_imessage_denied_no_prior_calls():
    ctx = _ctx("imessage__send")
    deny, reason = IMessageSendGate().verify(ctx)
    assert deny is True
    assert "contacts__search" in reason


def test_imessage_denied_contacts_search_without_name():
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": [_tc("contacts__search", {})]},
    )
    deny, reason = IMessageSendGate().verify(ctx)
    assert deny is True
    assert "contacts__search" in reason


def test_imessage_allowed_contacts_search_with_name_immediate():
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": [_tc("contacts__search", {"name": "Alice"})]},
        prompt_text="send message to Alice",
    )
    deny, _ = IMessageSendGate().verify(ctx)
    assert deny is False


def test_imessage_allowed_contacts_search_within_window():
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": [_tc("contacts__search", {"name": "Bob"})]},
        prompt_text="message Bob about the meeting",
    )
    deny, _ = IMessageSendGate().verify(ctx)
    assert deny is False


def test_imessage_allowed_no_prompt_text_skips_name_check():
    # prompt_text is empty — name check is skipped, gate passes on prereq alone
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": [_tc("contacts__search", {"name": "Alice"})]},
        prompt_text="",
    )
    deny, _ = IMessageSendGate().verify(ctx)
    assert deny is False


def test_imessage_denied_name_not_in_prompt():
    # contacts__search was for "Alice" but prompt mentions "Bob"
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": [_tc("contacts__search", {"name": "Alice"})]},
        prompt_text="send a message to Bob",
    )
    deny, reason = IMessageSendGate().verify(ctx)
    assert deny is True
    assert "Alice" in reason


def test_imessage_allowed_name_case_insensitive():
    # name check is case-insensitive
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": [_tc("contacts__search", {"name": "Alice"})]},
        prompt_text="Send iMessage to ALICE now",
    )
    deny, _ = IMessageSendGate().verify(ctx)
    assert deny is False


def test_imessage_allowed_name_substring_in_prompt():
    # "alice" appears as part of a longer word in the prompt
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": [_tc("contacts__search", {"name": "Alice Smith"})]},
        prompt_text="remind alice smith about tomorrow",
    )
    deny, _ = IMessageSendGate().verify(ctx)
    assert deny is False


def test_imessage_denied_contacts_search_stale():
    # contacts__search happened more than DEFAULT_WINDOW_S seconds ago — denied
    ctx = _ctx(
        "imessage__send",
        session_tools={"p1": [_tc("contacts__search", {"name": "Bob"}, ts=_stale_ts())]},
    )
    deny, reason = IMessageSendGate().verify(ctx)
    assert deny is True
    assert "contacts__search" in reason


def test_imessage_allowed_contacts_search_in_current_calls():
    ctx = _ctx(
        "imessage__send",
        current_tools=["contacts__search"],
    )
    # current_calls built without tool_input — name is empty, should deny
    deny, _ = IMessageSendGate().verify(ctx)
    assert deny is True  # no name arg in current_calls (built without it)


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
        session_tools={"p1": [_tc("contacts__search")]},
        session_prompt_ids=["p1"],
        prompt_id="p1",
    )
    deny, _ = MailComposeGate().verify(ctx)
    assert deny is False


# ---------------------------------------------------------------------------
# MailDeleteGate
# ---------------------------------------------------------------------------

def test_mail_delete_denied_without_mail_read():
    ctx = _ctx("mail__delete")
    deny, reason = MailDeleteGate().verify(ctx)
    assert deny is True
    assert "mail__read" in reason


def test_mail_delete_allowed_after_mail_read():
    ctx = _ctx(
        "mail__delete",
        session_tools={"p1": [_tc("mail__read")]},
    )
    deny, _ = MailDeleteGate().verify(ctx)
    assert deny is False


def test_mail_delete_allowed_mail_read_within_window():
    # mail__read happened recently — allowed
    ctx = _ctx(
        "mail__delete",
        session_tools={"p1": [_tc("mail__read")]},
    )
    deny, _ = MailDeleteGate().verify(ctx)
    assert deny is False


def test_mail_delete_denied_mail_read_stale():
    # mail__read happened more than DEFAULT_WINDOW_S seconds ago — denied
    ctx = _ctx(
        "mail__delete",
        session_tools={"p1": [_tc("mail__read", ts=_stale_ts())]},
    )
    deny, reason = MailDeleteGate().verify(ctx)
    assert deny is True
    assert "mail__read" in reason


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
# tasks__create body format gate (_check_task_body_format in dispatcher.py)
# ---------------------------------------------------------------------------

from hooks.dispatcher import _check_task_body_format

_VALID_BUG_BODY = (
    "Type: bug\n\n"
    "Task:\nGate not enforcing sections\n\n"
    "Resolution:\nAdded section check.\n\n"
    "Cause:\nNo enforcement existed.\n\n"
    "Files:\ndispatcher.py"
)

_VALID_FEATURE_BODY = (
    "Type: feature\n\n"
    "Task:\nAdd 4-type body gate\n\n"
    "Resolution:\nBranch on Type: value; require per-type sections.\n\n"
    "Motivation:\nOld gate only handled feature vs default.\n\n"
    "Files:\nhooks/dispatcher.py, tests/test_gates.py"
)

_VALID_RESEARCH_BODY = (
    "Type: research\n\n"
    "Task:\nWhy is Bosch rallying while Nifty falls?\n\n"
    "Finding:\nCapex cycle + import substitution tailwind.\n\n"
    "Context:\nNifty down 5% MTD; Bosch up 8%.\n\n"
    "Files:\n"
)

_VALID_MISC_BODY = (
    "Type: misc\n\n"
    "Task:\nUpdate skill docs\n\n"
    "Resolution:\nSynced task-create and task-framework skills.\n\n"
    "Notes:\nNo code changes.\n\n"
    "Files:\nskills/task-create/skill.md"
)


# --- common ---

def test_task_body_empty_string_denied():
    result = _check_task_body_format({"body": ""})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "Type:" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_task_body_missing_key_denied():
    result = _check_task_body_format({})
    assert result["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_task_body_missing_type_denied():
    body = "Task:\nfoo\n\nResolution:\nbar\n\nCause:\nbaz\n\nFiles:\nfoo.py"
    result = _check_task_body_format({"body": body})
    assert result is not None
    reason = result["hookSpecificOutput"]["permissionDecisionReason"]
    assert "Type:" in reason


def test_task_body_unknown_type_denied():
    body = "Type: unknown\n\nTask:\nfoo"
    result = _check_task_body_format({"body": body})
    assert result is not None
    reason = result["hookSpecificOutput"]["permissionDecisionReason"]
    assert "unknown" in reason.lower()


# --- bug template ---

def test_task_body_valid_bug_returns_none():
    assert _check_task_body_format({"body": _VALID_BUG_BODY}) is None


def test_task_body_bug_missing_cause():
    body = "Type: bug\n\nTask:\nfoo\n\nResolution:\nbar\n\nFiles:\nbaz.py"
    result = _check_task_body_format({"body": body})
    assert result is not None
    assert "Cause:" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_task_body_bug_missing_files():
    body = "Type: bug\n\nTask:\nfoo\n\nResolution:\nbar\n\nCause:\nbaz"
    result = _check_task_body_format({"body": body})
    assert "Files:" in result["hookSpecificOutput"]["permissionDecisionReason"]


# --- feature template ---

def test_task_body_valid_feature_returns_none():
    assert _check_task_body_format({"body": _VALID_FEATURE_BODY}) is None


def test_task_body_feature_missing_motivation():
    body = "Type: feature\n\nTask:\nfoo\n\nResolution:\nbar\n\nFiles:\nbaz.py"
    result = _check_task_body_format({"body": body})
    assert result is not None
    assert "Motivation:" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_task_body_feature_does_not_require_cause():
    result = _check_task_body_format({"body": _VALID_FEATURE_BODY})
    assert result is None


# --- research template ---

def test_task_body_valid_research_returns_none():
    assert _check_task_body_format({"body": _VALID_RESEARCH_BODY}) is None


def test_task_body_research_missing_finding():
    body = "Type: research\n\nTask:\nfoo\n\nContext:\nbar\n\nFiles:\n"
    result = _check_task_body_format({"body": body})
    assert result is not None
    assert "Finding:" in result["hookSpecificOutput"]["permissionDecisionReason"]


def test_task_body_research_does_not_require_resolution():
    result = _check_task_body_format({"body": _VALID_RESEARCH_BODY})
    assert result is None


# --- misc template ---

def test_task_body_valid_misc_returns_none():
    assert _check_task_body_format({"body": _VALID_MISC_BODY}) is None


def test_task_body_misc_missing_notes():
    body = "Type: misc\n\nTask:\nfoo\n\nResolution:\nbar\n\nFiles:\nbaz.py"
    result = _check_task_body_format({"body": body})
    assert result is not None
    assert "Notes:" in result["hookSpecificOutput"]["permissionDecisionReason"]


# ---------------------------------------------------------------------------
# GitCommitGate
# ---------------------------------------------------------------------------

def _git_ctx(command: str) -> GateContext:
    return _ctx(tool_name="Bash", tool_input={"command": command})


def test_git_commit_gate_registered():
    assert "Bash" in GATES
    assert isinstance(GATES["Bash"], GitCommitGate)


def test_git_commit_denied_no_task_id():
    ctx = _git_ctx('git commit -m "fix: something"')
    deny, reason = GitCommitGate().verify(ctx)
    assert deny
    assert "task:<id>" in reason


def test_git_commit_allowed_with_task_id_in_body():
    ctx = _git_ctx('git commit -m "$(cat <<\'EOF\'\nfix: something\n\ntask:12168f99\nEOF\n)"')
    deny, _ = GitCommitGate().verify(ctx)
    assert not deny


def test_git_commit_allowed_with_task_id_inline():
    ctx = _git_ctx('git commit -m "fix: something\n\ntask:abcdef12"')
    deny, _ = GitCommitGate().verify(ctx)
    assert not deny


def test_git_local_sh_denied_no_task_id():
    ctx = _git_ctx('~/workspace/claude_for_mac_local/tools/git_local.sh -y "Fix auth bug"')
    deny, reason = GitCommitGate().verify(ctx)
    assert deny
    assert "task:<id>" in reason


def test_git_local_sh_allowed_with_task_id():
    ctx = _git_ctx('~/workspace/claude_for_mac_local/tools/git_local.sh -y "Fix auth bug\n\ntask:abcdef12"')
    deny, _ = GitCommitGate().verify(ctx)
    assert not deny


def test_non_commit_bash_always_allowed():
    ctx = _git_ctx("ls -la /tmp")
    deny, _ = GitCommitGate().verify(ctx)
    assert not deny


def test_git_status_bash_always_allowed():
    ctx = _git_ctx("git status --short")
    deny, _ = GitCommitGate().verify(ctx)
    assert not deny


def test_git_commit_via_check_dispatch():
    ctx = _git_ctx('git commit -m "no task id here"')
    deny, reason = check("Bash", ctx)
    assert deny
    assert "task:<id>" in reason


def test_git_dash_C_commit_denied_no_task_id():
    """git -C <path> commit must be caught — real-world form used by Claude Code."""
    ctx = _git_ctx('git -C /Users/foo/workspace/claude-hooks commit -m "fix: something"')
    deny, reason = GitCommitGate().verify(ctx)
    assert deny
    assert "task:<id>" in reason


def test_git_dash_C_commit_allowed_with_task_id():
    ctx = _git_ctx(
        'git -C /Users/foo/workspace/claude-hooks commit -m "$(cat <<\'EOF\'\n'
        'fix: something\n\ntask:abcdef12\nEOF\n)"'
    )
    deny, _ = GitCommitGate().verify(ctx)
    assert not deny


def test_git_dash_C_amend_denied_no_task_id():
    ctx = _git_ctx('git -C /path commit --amend -m "fix: something"')
    deny, _ = GitCommitGate().verify(ctx)
    assert deny


def test_git_dash_C_log_always_allowed():
    ctx = _git_ctx("git -C /path log --oneline -5")
    deny, _ = GitCommitGate().verify(ctx)
    assert not deny


def test_git_add_and_commit_denied_no_task_id():
    """Compound add+commit command without task ID must be blocked."""
    ctx = _git_ctx(
        'git -C /path add file.py && git -C /path commit -m "$(cat <<\'EOF\'\nfix\nEOF\n)"'
    )
    deny, _ = GitCommitGate().verify(ctx)
    assert deny


def test_git_add_and_commit_allowed_with_task_id():
    ctx = _git_ctx(
        'git -C /path add file.py && git -C /path commit -m "$(cat <<\'EOF\'\nfix\n\ntask:abc12345\nEOF\n)"'
    )
    deny, _ = GitCommitGate().verify(ctx)
    assert not deny


# ---------------------------------------------------------------------------
# GitCommitMcpGate
# ---------------------------------------------------------------------------

def _mcp_git_ctx(task_id: str = "", message: str = "fix: something") -> GateContext:
    return _ctx(tool_name="git__commit", tool_input={"message": message, "task_id": task_id})


def test_git_commit_mcp_gate_registered():
    assert "git__commit" in GATES
    assert isinstance(GATES["git__commit"], GitCommitMcpGate)


def test_git_commit_mcp_denied_no_task_id():
    deny, reason = GitCommitMcpGate().verify(_mcp_git_ctx(task_id=""))
    assert deny
    assert "task_id" in reason


def test_git_commit_mcp_denied_whitespace_task_id():
    deny, _ = GitCommitMcpGate().verify(_mcp_git_ctx(task_id="   "))
    assert deny


def test_git_commit_mcp_allowed_with_task_id():
    deny, _ = GitCommitMcpGate().verify(_mcp_git_ctx(task_id="task:abc12345"))
    assert not deny


def test_git_commit_mcp_allowed_bare_id():
    deny, _ = GitCommitMcpGate().verify(_mcp_git_ctx(task_id="abc12345"))
    assert not deny


def test_git_commit_mcp_via_check_dispatch():
    deny, reason = check("git__commit", _mcp_git_ctx(task_id=""))
    assert deny
    assert "task_id" in reason


# ---------------------------------------------------------------------------
# JiraHierarchyGate
# ---------------------------------------------------------------------------

def _jira_ctx(issue_type: str, parent_id: str = "") -> GateContext:
    return GateContext(
        tool_name="tasks__create",
        tool_input={"issue_type": issue_type, "parent_id": parent_id},
        current_calls=[],
        session_tools=OrderedDict(),
        session_prompt_ids=[],
        prompt_id="test-prompt",
    )


def test_jira_hierarchy_gate_registered():
    assert "tasks__create" in GATES


def test_epic_without_parent_allowed():
    deny, _ = JiraHierarchyGate().verify(_jira_ctx("epic"))
    assert not deny


def test_epic_with_parent_denied():
    deny, reason = JiraHierarchyGate().verify(_jira_ctx("epic", parent_id="abc123"))
    assert deny
    assert "epics cannot have a parent" in reason


def test_story_without_parent_denied():
    deny, reason = JiraHierarchyGate().verify(_jira_ctx("story"))
    assert deny
    assert "requires a parent_id" in reason


def test_task_without_parent_denied():
    deny, reason = JiraHierarchyGate().verify(_jira_ctx("task"))
    assert deny
    assert "requires a parent_id" in reason


def test_bug_without_parent_denied():
    deny, reason = JiraHierarchyGate().verify(_jira_ctx("bug"))
    assert deny
    assert "requires a parent_id" in reason


def test_subtask_without_parent_denied():
    deny, reason = JiraHierarchyGate().verify(_jira_ctx("subtask"))
    assert deny
    assert "requires a parent_id" in reason


def test_story_with_epic_parent_allowed(tmp_path, monkeypatch):
    import sqlite3
    from unittest.mock import patch
    db = tmp_path / "proj_tasks.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE open_tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT DEFAULT '', tags TEXT DEFAULT '', status TEXT DEFAULT 'open', issue_type TEXT DEFAULT 'task', parent_id TEXT DEFAULT NULL, created_at TIMESTAMP DEFAULT (datetime('now')), updated_at TIMESTAMP DEFAULT (datetime('now')))")
    conn.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, prompt_id TEXT DEFAULT '', session_id TEXT DEFAULT '', turn INTEGER DEFAULT 0, summary TEXT DEFAULT '', tools TEXT DEFAULT '', related TEXT DEFAULT '', logged_at TIMESTAMP DEFAULT (datetime('now')))")
    conn.execute("CREATE TABLE task_edges (from_id TEXT, to_id TEXT, relation_type TEXT, created_at TIMESTAMP DEFAULT (datetime('now')), PRIMARY KEY (from_id, to_id, relation_type))")
    conn.execute("INSERT INTO open_tasks (id, title, issue_type) VALUES ('epic01', 'My Epic', 'epic')")
    conn.commit(); conn.close()
    with patch("src.tools.tasks._DB", db):
        deny, _ = JiraHierarchyGate().verify(_jira_ctx("story", parent_id="epic01"))
    assert not deny


def test_story_with_story_parent_denied(tmp_path):
    import sqlite3
    from unittest.mock import patch
    db = tmp_path / "proj_tasks.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE open_tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT DEFAULT '', tags TEXT DEFAULT '', status TEXT DEFAULT 'open', issue_type TEXT DEFAULT 'task', parent_id TEXT DEFAULT NULL, created_at TIMESTAMP DEFAULT (datetime('now')), updated_at TIMESTAMP DEFAULT (datetime('now')))")
    conn.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, prompt_id TEXT DEFAULT '', session_id TEXT DEFAULT '', turn INTEGER DEFAULT 0, summary TEXT DEFAULT '', tools TEXT DEFAULT '', related TEXT DEFAULT '', logged_at TIMESTAMP DEFAULT (datetime('now')))")
    conn.execute("CREATE TABLE task_edges (from_id TEXT, to_id TEXT, relation_type TEXT, created_at TIMESTAMP DEFAULT (datetime('now')), PRIMARY KEY (from_id, to_id, relation_type))")
    conn.execute("INSERT INTO open_tasks (id, title, issue_type) VALUES ('story01', 'A Story', 'story')")
    conn.commit(); conn.close()
    with patch("src.tools.tasks._DB", db):
        deny, reason = JiraHierarchyGate().verify(_jira_ctx("story", parent_id="story01"))
    assert deny
    assert "epic" in reason


def test_subtask_with_story_parent_allowed(tmp_path):
    import sqlite3
    from unittest.mock import patch
    db = tmp_path / "proj_tasks.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE open_tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT DEFAULT '', tags TEXT DEFAULT '', status TEXT DEFAULT 'open', issue_type TEXT DEFAULT 'task', parent_id TEXT DEFAULT NULL, created_at TIMESTAMP DEFAULT (datetime('now')), updated_at TIMESTAMP DEFAULT (datetime('now')))")
    conn.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, prompt_id TEXT DEFAULT '', session_id TEXT DEFAULT '', turn INTEGER DEFAULT 0, summary TEXT DEFAULT '', tools TEXT DEFAULT '', related TEXT DEFAULT '', logged_at TIMESTAMP DEFAULT (datetime('now')))")
    conn.execute("CREATE TABLE task_edges (from_id TEXT, to_id TEXT, relation_type TEXT, created_at TIMESTAMP DEFAULT (datetime('now')), PRIMARY KEY (from_id, to_id, relation_type))")
    conn.execute("INSERT INTO open_tasks (id, title, issue_type) VALUES ('story01', 'A Story', 'story')")
    conn.commit(); conn.close()
    with patch("src.tools.tasks._DB", db):
        deny, _ = JiraHierarchyGate().verify(_jira_ctx("subtask", parent_id="story01"))
    assert not deny


def test_subtask_with_epic_parent_denied(tmp_path):
    import sqlite3
    from unittest.mock import patch
    db = tmp_path / "proj_tasks.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE open_tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT DEFAULT '', tags TEXT DEFAULT '', status TEXT DEFAULT 'open', issue_type TEXT DEFAULT 'task', parent_id TEXT DEFAULT NULL, created_at TIMESTAMP DEFAULT (datetime('now')), updated_at TIMESTAMP DEFAULT (datetime('now')))")
    conn.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, prompt_id TEXT DEFAULT '', session_id TEXT DEFAULT '', turn INTEGER DEFAULT 0, summary TEXT DEFAULT '', tools TEXT DEFAULT '', related TEXT DEFAULT '', logged_at TIMESTAMP DEFAULT (datetime('now')))")
    conn.execute("CREATE TABLE task_edges (from_id TEXT, to_id TEXT, relation_type TEXT, created_at TIMESTAMP DEFAULT (datetime('now')), PRIMARY KEY (from_id, to_id, relation_type))")
    conn.execute("INSERT INTO open_tasks (id, title, issue_type) VALUES ('epic01', 'Epic', 'epic')")
    conn.commit(); conn.close()
    with patch("src.tools.tasks._DB", db):
        deny, reason = JiraHierarchyGate().verify(_jira_ctx("subtask", parent_id="epic01"))
    assert deny
    assert "epic" not in reason.split("'")[0]  # denied because epic is not a valid subtask parent


def test_parent_not_found_denied(tmp_path):
    import sqlite3
    from unittest.mock import patch
    db = tmp_path / "proj_tasks.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE open_tasks (id TEXT PRIMARY KEY, title TEXT NOT NULL, body TEXT DEFAULT '', tags TEXT DEFAULT '', status TEXT DEFAULT 'open', issue_type TEXT DEFAULT 'task', parent_id TEXT DEFAULT NULL, created_at TIMESTAMP DEFAULT (datetime('now')), updated_at TIMESTAMP DEFAULT (datetime('now')))")
    conn.execute("CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, prompt_id TEXT DEFAULT '', session_id TEXT DEFAULT '', turn INTEGER DEFAULT 0, summary TEXT DEFAULT '', tools TEXT DEFAULT '', related TEXT DEFAULT '', logged_at TIMESTAMP DEFAULT (datetime('now')))")
    conn.execute("CREATE TABLE task_edges (from_id TEXT, to_id TEXT, relation_type TEXT, created_at TIMESTAMP DEFAULT (datetime('now')), PRIMARY KEY (from_id, to_id, relation_type))")
    conn.commit(); conn.close()
    with patch("src.tools.tasks._DB", db):
        deny, reason = JiraHierarchyGate().verify(_jira_ctx("story", parent_id="doesnotexist"))
    assert deny
    assert "not found" in reason
