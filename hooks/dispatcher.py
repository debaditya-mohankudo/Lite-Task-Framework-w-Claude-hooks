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
        for ev in ctx["task_context"]:
            turn    = ev.get("turn", "?")
            summary = ev.get("summary", "").strip()
            tools   = ev.get("tools", "").strip()
            line = f"- turn {turn}"
            if summary:
                line += f": {summary}"
            if tools:
                line += f" [{tools}]"
            lines.append(line)
        lines.append("")

    if ctx.get("task_commits"):
        lines.append("## Task commits")
        for c in ctx["task_commits"]:
            sha     = c.get("sha", "?")
            date    = c.get("date", "")
            subject = c.get("subject", "").strip()
            lines.append(f"- {sha} {date}: {subject}")
        lines.append("")

    if ctx.get("prompt_context"):
        lines.append("## Session context")
        for sid, text in ctx["prompt_context"].items():
            lines.append(f"- [{sid[:8]}] {text}")
        lines.append("")

    return "\n".join(lines).strip()


def _handle_user_prompt_submit(hook_input: dict) -> dict | None:
    from src.config import config as _cfg
    cwd    = os.environ.get("CLAUDE_CWD") or os.getcwd()
    prompt = _extract_prompt(hook_input)

    if not prompt:
        return None

    session_id = _get_claude_session_id(hook_input)

    from langchain_learning.session_graph import run_session
    ctx = run_session(prompt=prompt, session_id=session_id, cwd=cwd)

    system_prompt = _format_system_prompt(ctx)

    task_history_chars = sum(
        len(ev.get("summary", "")) + len(ev.get("tools", ""))
        for ev in ctx.get("task_context", [])
    )
    log.info(
        "UserPromptSubmit: domains=%s memories=%d tools=%d active_task=%s "
        "task_turns=%d task_history_chars=%d task_commits=%d prompt_context_ids=%s",
        ctx.get("domains", []), len(ctx.get("memories", [])), len(ctx.get("tool_hints", [])),
        ctx.get("active_task_id", ""),
        len(ctx.get("task_context", [])), task_history_chars, len(ctx.get("task_commits", [])),
        list(ctx.get("prompt_context", {}).keys()),
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
        return None

    short_name = strip_mcp_prefix(tool_name) or tool_name
    if short_name.startswith("memory__"):
        return None

    from langchain_learning.session_graph import run_post_tool, get_session_graph, _config
    try:
        state = get_session_graph().get_state(_config(session_id))
        prompt = (state.values.get("prompt") or "") if state and state.values else ""
    except Exception:
        prompt = ""

    run_post_tool(
        tool_name=short_name,
        tool_input=tool_input if isinstance(tool_input, dict) else {},
        tool_result=tool_response,
        session_id=session_id,
        duration_ms=duration_ms,
        prompt=prompt,
    )
    return None


# ---------------------------------------------------------------------------
# PreToolUse
# ---------------------------------------------------------------------------

_FAIL_CLOSED_TOOLS = {"imessage__send", "mail__compose"}


def _handle_pre_tool_use(hook_input: dict) -> dict | None:
    from core.tool_registry import strip_mcp_prefix

    tool_name  = hook_input.get("tool_name", "")
    session_id = hook_input.get("session_id", "")

    if not tool_name or not session_id or not tool_name.startswith("mcp__"):
        return None

    short_name = strip_mcp_prefix(tool_name)
    if not short_name or short_name.startswith("memory__"):
        return None

    from langchain_learning.session_graph import run_gate
    result = run_gate(
        tool_name=short_name,
        tool_input=hook_input.get("tool_input") or {},
        session_id=session_id,
    )

    if result["gate_denied"]:
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": result["gate_reason"],
            }
        }
    return None


# ---------------------------------------------------------------------------
# Stop
# ---------------------------------------------------------------------------

def _handle_stop(hook_input: dict) -> dict | None:
    session_id = hook_input.get("session_id", "")
    if not session_id:
        return None

    from langchain_learning.session_graph import run_stop
    run_stop(session_id=session_id)
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
