#!/usr/bin/env python3
"""
Single entry point for all Claude Code hook events.

Usage:
    dispatcher.py <hook_event>

    hook_event: UserPromptSubmit | PostToolUse | PreToolUse | Stop

Each handler is responsible for one Claude Code hook type. All share:
  - sys.path setup
  - read_stdin / write_json_to_stdout
  - flush_logs in finally
  - dev_mode sys.exit(2) on error

Session graph call graph:
  UserPromptSubmit  → run_session()
  PostToolUse       → run_post_tool()
  PreToolUse        → run_gate()
  Stop              → run_stop()
"""
import os
import re
import sys
import time
from pathlib import Path

_PROJECT_ROOT = Path.home() / "workspace/claude-hooks"
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from langchain_learning.config import config as _lc_cfg
from src.logger import flush_logs, setup
from utils import read_stdin, write_json_to_stdout

log = setup("dispatcher")

# ---------------------------------------------------------------------------
# Shared extractors
# ---------------------------------------------------------------------------

def _get_claude_session_id(hook_input: dict) -> str:
    """Extract the Claude Code session UUID — the authoritative session identity."""
    return hook_input.get("session_id", "")


def _extract_prompt(hook_input: dict) -> str:
    prompt = hook_input.get("prompt", "")
    if not prompt:
        msg     = hook_input.get("message") or {}
        content = msg.get("content", "")
        if isinstance(content, str):
            prompt = content
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    prompt += block.get("text", "")
    # Strip injected XML context tags before storing (avoids noise in tool hints)
    prompt = re.sub(r"<[a-z_]+>[^<]{0,2000}</[a-z_]+>\n?", "", prompt, flags=re.DOTALL)
    return prompt.strip()


# ---------------------------------------------------------------------------
# UserPromptSubmit
# ---------------------------------------------------------------------------

def _format_system_prompt(ctx: dict) -> str:
    """Convert SessionState dict into the injected system prompt block."""
    lines: list[str] = []

    session_id = ctx.get("session_id", "")
    prompt_id  = ctx.get("prompt_id", "")
    if session_id or prompt_id:
        lines.append("## Turn state")
        if session_id:
            lines.append(f"- session_id: {session_id}")
        if prompt_id:
            lines.append(f"- prompt_id: {prompt_id}")
        lines.append("")

    if ctx["domains"]:
        lines.append(f"# Active domains: {', '.join(ctx['domains'])}")
        lines.append("")

    if ctx["memories"]:
        lines.append("## Injected memories")
        for mem in ctx["memories"]:
            name   = mem.get("name", "?")
            domain = mem.get("domain", "")
            body   = mem.get("body", "").strip()
            lines.append(f"### {name} [{domain}]")
            if body:
                lines.append(body)
            lines.append("")

    if ctx["tool_hints"]:
        lines.append("## Suggested tools")
        for hint in ctx["tool_hints"]:
            tool  = hint.get("tool_name", "?")
            skill = hint.get("skill", "")
            count = hint.get("count", 0)
            lines.append(f"- `{tool}` (skill={skill}, used={count}x)")
        lines.append("")

    if ctx.get("active_task_id"):
        title = ctx.get("active_task_title", "")
        lines.append(f"## Active task: task:{ctx['active_task_id']}" + (f" — {title}" if title else ""))
        body = (ctx.get("task_body") or "").strip()
        if body:
            lines.append(body)
        lines.append("")

    if ctx.get("mid_task_decisions"):
        lines.append("## Task decisions")
        for decision in ctx["mid_task_decisions"]:
            lines.append(f"- {decision}")
        lines.append("")

    if ctx.get("task_memories"):
        lines.append("## Task memories")
        for mem in ctx["task_memories"]:
            name   = mem.get("name", "?")
            domain = mem.get("domain", "")
            body   = mem.get("body", "").strip()
            lines.append(f"### {name} [{domain}]")
            if body:
                lines.append(body)
            lines.append("")

    if ctx.get("task_context"):
        lines.append("## Task history")
        task_ctx     = ctx["task_context"]
        unique_sids  = {ev.get("session_id", "") for ev in task_ctx}
        multi_session = len(unique_sids) > 1
        for ev in task_ctx:
            turn    = ev.get("turn", "?")
            summary = ev.get("summary", "").strip()
            tools   = ev.get("tools", "").strip()
            sid     = (ev.get("session_id") or "")[:8]
            line = f"- [{sid}] turn {turn}" if multi_session else f"- turn {turn}"
            if summary:
                line += f": {summary}"
            if tools:
                line += f" [{tools}]"
            lines.append(line)
        lines.append("")

    if ctx.get("task_rag_chunks"):
        lines.append("## Relevant code")
        for c in ctx["task_rag_chunks"]:
            name = c.get("name", "")
            mod  = c.get("module", "?")
            file = c.get("file", "")
            line = c.get("line", "")
            label = f"`{name}`" if name else f"`{mod}`"
            loc = f"{file}:{line}" if line else file
            lines.append(f"- {label} — {loc}")
        lines.append("")

    if ctx.get("related_tasks"):
        lines.append("## Related past tasks")
        for t in ctx["related_tasks"]:
            lines.append(f"- {t['id']}: {t['title']}")
            if t.get("body_snippet"):
                lines.append(f"  {t['body_snippet']}")
        lines.append("")

    return "\n".join(lines).strip()


def _handle_user_prompt_submit(hook_input: dict) -> dict | None:
    cwd        = os.environ.get("CLAUDE_CWD") or os.getcwd()
    prompt     = _extract_prompt(hook_input)
    session_id = _get_claude_session_id(hook_input)

    log.info("UPS enter: session=%s cwd=%s prompt_len=%d", session_id[:8], Path(cwd).name, len(prompt))

    if not prompt:
        log.info("UPS skip: empty prompt")
        return None

    t0 = time.monotonic()
    from langchain_learning.session_graph import run_session
    ctx = run_session(prompt=prompt, session_id=session_id, cwd=cwd)
    elapsed_ms = (time.monotonic() - t0) * 1000

    system_prompt = _format_system_prompt(ctx)

    task_history_chars = sum(
        len(ev.get("summary", "")) + len(ev.get("tools", ""))
        for ev in ctx.get("task_context", [])
    )
    log.info(
        "UPS done: session=%s elapsed_ms=%.0f domains=%s memories=%d tools=%d "
        "active_task=%s task_turns=%d task_history_chars=%d rag_chunks=%s related=%s "
        "prompt_chars=%d",
        session_id[:8], elapsed_ms,
        ctx.get("domains", []), len(ctx.get("memories", [])), len(ctx.get("tool_hints", [])),
        ctx.get("active_task_id", ""),
        len(ctx.get("task_context", [])), task_history_chars,
        [c.get("module", "?").split(".")[-1] for c in ctx.get("task_rag_chunks", [])],
        [t["id"] for t in ctx.get("related_tasks", [])],
        len(system_prompt),
    )

    if system_prompt:
        return {"hookSpecificOutput": {"additionalSystemPrompt": system_prompt}}
    return None


# ---------------------------------------------------------------------------
# PostToolUse
# ---------------------------------------------------------------------------

def _handle_post_tool_use(hook_input: dict) -> dict | None:
    from core.tool_registry import strip_mcp_prefix

    tool_name   = hook_input.get("tool_name", "")
    session_id  = hook_input.get("session_id", "")
    duration_ms = float(hook_input.get("duration_ms", 0))
    tool_input  = hook_input.get("tool_input", {})
    tool_response = hook_input.get("tool_response") or {}

    log.info("PTU enter: session=%s tool=%s duration_ms=%.0f", session_id[:8], tool_name, duration_ms)
    log.debug("tool_response raw: %r", tool_response)
    if not isinstance(tool_response, dict):
        tool_response = {"raw": str(tool_response)}

    # Claude Code wraps MCP responses: {"content": [{"type": "text", "text": "<json>"}]}
    if "content" in tool_response and isinstance(tool_response.get("content"), list):
        try:
            import json as _json
            text = tool_response["content"][0].get("text", "")
            parsed = _json.loads(text)
            if isinstance(parsed, dict):
                tool_response = parsed
        except Exception:
            pass

    if not tool_name or not tool_name.startswith("mcp__"):
        log.info("PTU skip: non-MCP tool=%s", tool_name)
        return None

    short_name = strip_mcp_prefix(tool_name) or tool_name
    if short_name.startswith("memory__"):
        log.info("PTU skip: memory tool=%s", short_name)
        return None

    from langchain_learning.session_graph import run_post_tool, get_session_graph, _config
    try:
        state = get_session_graph().get_state(_config(session_id))
        prompt = (state.values.get("prompt") or "") if state and state.values else ""
    except Exception:
        prompt = ""

    tool_input_clean = tool_input if isinstance(tool_input, dict) else {}

    t0 = time.monotonic()
    run_post_tool(
        tool_name=short_name,
        tool_input=tool_input_clean,
        tool_result=tool_response,
        session_id=session_id,
        duration_ms=duration_ms,
        prompt=prompt,
    )
    log.info("PTU done: session=%s tool=%s elapsed_ms=%.0f", session_id[:8], short_name, (time.monotonic() - t0) * 1000)
    return None


# ---------------------------------------------------------------------------
# PreToolUse
# ---------------------------------------------------------------------------

_FAIL_CLOSED_TOOLS = {"imessage__send", "mail__compose"}

# Required body sections per workflow type (issue_type is now a separate param).
_TASK_BODY_SECTIONS: dict[str, tuple[tuple[str, ...], str]] = {
    "feature": (
        ("Task:", "Resolution:", "Motivation:", "Files:"),
        "Task:\n<what is being built>\n\nResolution:\n<what was delivered>\n\nMotivation:\n<why this is needed>\n\nFiles:\n<file1>, <file2>",
    ),
    "bug": (
        ("Task:", "Resolution:", "Cause:", "Files:"),
        "Task:\n<what is broken>\n\nResolution:\n<what fixed it>\n\nCause:\n<root cause>\n\nFiles:\n<file1>, <file2>",
    ),
    "research": (
        ("Task:", "Finding:", "Context:", "Files:"),
        "Task:\n<question or hypothesis>\n\nFinding:\n<conclusion>\n\nContext:\n<background>\n\nFiles:\n(leave blank)",
    ),
    "misc": (
        ("Task:", "Resolution:", "Notes:", "Files:"),
        "Task:\n<what is being done>\n\nResolution:\n<outcome>\n\nNotes:\n<context>\n\nFiles:\n<file1>, <file2>",
    ),
}

_TASK_BODY_VALID_TYPES = ", ".join(_TASK_BODY_SECTIONS)


def _check_task_body_format(tool_input: dict) -> dict | None:
    """Deny tasks__create if body is missing required sections.

    Body workflow type is detected from a leading 'Type: <value>' line for
    backwards compatibility, but issue_type is now a separate param.
    Denies if Type is missing/unknown or required sections are absent.
    """
    import re
    body = (tool_input.get("body") or "").strip()
    if not body:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"tasks__create requires a body with Type: ({_TASK_BODY_VALID_TYPES}). "
                    f"See /task-create for templates."
                ),
            }
        }
    m = re.search(r"^Type:\s*(\w+)", body, re.MULTILINE)
    if not m:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"tasks__create body must start with 'Type: <type>'. "
                    f"Valid types: {_TASK_BODY_VALID_TYPES}. See /task-create for templates."
                ),
            }
        }
    task_type = m.group(1).lower()
    if task_type not in _TASK_BODY_SECTIONS:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"Unknown task type '{task_type}'. "
                    f"Valid types: {_TASK_BODY_VALID_TYPES}. See /task-create for templates."
                ),
            }
        }
    sections, fmt = _TASK_BODY_SECTIONS[task_type]
    missing = [s for s in sections if s not in body]
    if missing:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": (
                    f"tasks__create body (type={task_type}) is missing: {', '.join(missing)}.\n\n{fmt}"
                ),
            }
        }
    return None


def _handle_pre_tool_use(hook_input: dict) -> dict | None:
    from core.tool_registry import strip_mcp_prefix

    tool_name  = hook_input.get("tool_name", "")
    session_id = hook_input.get("session_id", "")

    log.info("PreTU enter: session=%s tool=%s", session_id[:8] if session_id else "?", tool_name)

    if not tool_name or not session_id:
        return None

    # Built-in tools (e.g. Bash) are gated directly by tool_name; MCP tools are stripped.
    if tool_name == "Bash":
        short_name = "Bash"
    elif tool_name.startswith("mcp__"):
        short_name = strip_mcp_prefix(tool_name)
        if not short_name or short_name.startswith("memory__"):
            return None
    else:
        return None

    if short_name == "tasks__create":
        denied = _check_task_body_format(hook_input.get("tool_input") or {})
        if denied:
            log.info("tasks__create denied: missing body sections")
            return denied

    from langchain_learning.session_graph import run_gate
    result = run_gate(
        tool_name=short_name,
        tool_input=hook_input.get("tool_input") or {},
        session_id=session_id,
    )

    if result["gate_denied"]:
        log.info("PreTU deny: session=%s tool=%s reason=%s", session_id[:8], short_name, result["gate_reason"][:80])
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": result["gate_reason"],
            }
        }
    log.info("PreTU allow: session=%s tool=%s", session_id[:8], short_name)
    return None


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------

def _handle_stop(hook_input: dict) -> dict | None:
    session_id = hook_input.get("session_id", "")
    if not session_id:
        return None

    log.info("Stop enter: session=%s", session_id[:8])
    t0 = time.monotonic()
    from langchain_learning.session_graph import run_stop
    run_stop(session_id=session_id)
    log.info("Stop done: session=%s elapsed_ms=%.0f", session_id[:8], (time.monotonic() - t0) * 1000)
    return None


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_HANDLERS = {
    "UserPromptSubmit": _handle_user_prompt_submit,
    "PostToolUse":      _handle_post_tool_use,
    "PreToolUse":       _handle_pre_tool_use,
    "Stop":             _handle_stop,
}


def main():
    hook_event = sys.argv[1] if len(sys.argv) > 1 else ""
    handler = _HANDLERS.get(hook_event)

    if not handler:
        log.error("Unknown hook event: %r", hook_event)
        write_json_to_stdout(error=f"Unknown hook event: {hook_event!r}")
        flush_logs()
        return

    hook_input: dict = {}
    try:
        hook_input = read_stdin()
        result = handler(hook_input)
        write_json_to_stdout(result if result else None)
    except Exception as e:
        log.error("%s handler failed: %s", hook_event, e)
        # PreToolUse fail-closed: irreversible tools must deny on any error
        if hook_event == "PreToolUse":
            from core.tool_registry import strip_mcp_prefix
            short = strip_mcp_prefix(hook_input.get("tool_name", "")) if hook_input else ""
            if short in _FAIL_CLOSED_TOOLS:
                write_json_to_stdout({
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": f"Gate check failed (internal error) — {short} blocked for safety.",
                    }
                })
                flush_logs()
                return
        write_json_to_stdout(error=f"{hook_event} handler failed: {e}")
        flush_logs()
        if _lc_cfg.dev_mode:
            sys.exit(2)
    finally:
        flush_logs()


if __name__ == "__main__":
    main()
