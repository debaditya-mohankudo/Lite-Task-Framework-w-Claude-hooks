#!/usr/bin/env python3
"""
UserPromptSubmit hook — runs the LangGraph session graph in-process.

Pipeline (LangGraph StateGraph):
  load_turn → load_memories → load_prompt_context → load_classifier_config
    → cwd_domain_detect → keyword_score → combination_score
    → memory_domain_signal → apply_threshold
    → score_tools (optional) → set_prompt_id → END

Side effects:
  - Writes current_prompt_keywords.tmp (used by PostToolUse hook)
  - Writes current_prompt_text.tmp (used by stop_hook)
"""
import os
import re
import sys
import uuid
from pathlib import Path

# Ensure project root is on sys.path so langchain_learning is importable
_PROJECT_ROOT = Path.home() / "workspace/claude-hooks"
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.config import config as _cfg
from langchain_learning.config import config as _lc_cfg
from src.logger import flush_logs
from sqlite_log_handler import setup
from utils import read_stdin, write_json_to_stdout

from langchain_learning.session_graph import run_session

log = setup("memory_loader_lc")

_VAULT_INDEX_DB  = _cfg.icloud_db_dir / "vault_index.sqlite"
_PROMPT_KW_TMP   = Path.home() / ".claude/current_prompt_keywords.tmp"
_PROMPT_TEXT_TMP = Path.home() / ".claude/current_prompt_text.tmp"
_PROMPT_ID_TMP   = _cfg.prompt_id_tmp


# ---------------------------------------------------------------------------
# Prompt extraction — mirrors PromptHandler.extract_prompt()
# ---------------------------------------------------------------------------

def _extract_prompt(hook_input: dict) -> str:
    import re as _re
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
    prompt = _re.sub(r"<[a-z_]+>[^<]{0,2000}</[a-z_]+>\n?", "", prompt, flags=_re.DOTALL)
    return prompt.strip()


# ---------------------------------------------------------------------------
# Side-effect helpers (preserved from original hook pipeline)
# ---------------------------------------------------------------------------

def _write_vault_keywords(prompt: str) -> None:
    """Intersect prompt tokens with vault_keyword_hints → temp file."""
    if not _VAULT_INDEX_DB.exists():
        _PROMPT_KW_TMP.write_text("")
        return
    try:
        import sqlite3
        tokens = set(re.findall(r"[a-z0-9_/-]+", prompt.lower()))
        with sqlite3.connect(f"file:{_VAULT_INDEX_DB}?mode=ro", uri=True) as conn:
            known = {r[0].lower() for r in conn.execute("SELECT keyword FROM vault_keyword_hints")}
        matched = sorted(tokens & known)
        _PROMPT_KW_TMP.write_text(",".join(matched))
        log.debug("vault keywords: %d matched from %d tokens", len(matched), len(tokens))
    except Exception as exc:
        log.warning("vault keyword lookup failed: %s", exc)
        _PROMPT_KW_TMP.write_text("")


# ---------------------------------------------------------------------------
# Format pipeline output → additionalSystemPrompt string
# ---------------------------------------------------------------------------

def _format_system_prompt(ctx: dict) -> str:
    """Convert SessionState dict into the injected system prompt block.

    Sections (all conditional on non-empty data):
      ## Turn state         — session_id and prompt_id for this turn
      # Active domains      — detected domain tags
      ## Injected memories  — scored memories from MEMORY.sqlite
      ## Suggested tools    — top tool hints from tool_hints.sqlite
      ## Session context    — top-2 session_summaries snippets by keyword score
    """
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

    if ctx.get("prompt_context"):
        lines.append("## Session context")
        for sid, text in ctx["prompt_context"].items():
            lines.append(f"- [{sid[:8]}] {text}")
        lines.append("")

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    try:
        hook_input = read_stdin()
        cwd        = os.environ.get("CLAUDE_CWD") or os.getcwd()
        prompt     = _extract_prompt(hook_input)

        if not prompt:
            write_json_to_stdout()
            return

        # side effects — preserve compatibility with PostToolUse + stop_hook
        _write_vault_keywords(prompt)
        _PROMPT_TEXT_TMP.write_text(prompt.strip())

        # session_id from Claude Code hook input — passed into LangGraph for
        # turn tracking (load_turn node reads actual turn from sessions.db)
        session_id = hook_input.get("session_id", "")

        # track session boundary: write on first turn, cleared by stop_hook at session end
        if not _PROMPT_ID_TMP.exists():
            _PROMPT_ID_TMP.write_text(session_id or str(uuid.uuid4()))
            log.debug("session started: session_id=%s", session_id)

        ctx = run_session(prompt=prompt, session_id=session_id, cwd=cwd)

        system_prompt = _format_system_prompt(ctx)

        log.info(
            "lc hook: domains=%s memories=%d tools=%d prompt_context_ids=%s",
            ctx.get("domains", []), len(ctx.get("memories", [])), len(ctx.get("tool_hints", [])),
            list(ctx.get("prompt_context", {}).keys()),
        )

        if system_prompt:
            write_json_to_stdout({"hookSpecificOutput": {"additionalSystemPrompt": system_prompt}})
        else:
            write_json_to_stdout()

    except Exception as e:
        log.error("memory_loader_lc failed: %s", e)
        write_json_to_stdout(error=f"memory_loader_lc failed: {e}")
        flush_logs()
        if _lc_cfg.dev_mode:
            sys.exit(2)
    finally:
        flush_logs()


if __name__ == "__main__":
    main()
