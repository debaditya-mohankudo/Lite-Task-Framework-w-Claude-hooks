#!/usr/bin/env python3
"""
UserPromptSubmit hook — LangChain variant (Option A).

Replaces the FastAPI server round-trip with a direct LCEL pipeline call.
No HTTP, no server dependency — the LangChain pipeline runs in-process.

LangChain concepts in play:
  - SQLiteMemoryRetriever  (BaseRetriever) — scores MEMORY.sqlite
  - DomainClassifier       (RunnableLambda + keyword signals) — detects domains
  - ToolHintsRetriever     (EnsembleRetriever + BM25) — retrieves tool hints
  - session context step   (RunnableLambda) — top-2 session_summaries by keyword score
  - build_memory_pipeline  (LCEL | pipe) — composes all four as parallel branches

Why this is cleaner than the original hook:
  - No HTTP round-trip → no timeout risk, no server startup dependency
  - Pipeline is a typed Runnable — inputs/outputs are explicit
  - Retrieval logic is testable in isolation (see tests/test_langchain_pipeline.py)
  - Adding a new retrieval step = one line in pipeline.py, not a FastAPI route

Difference from memory_loader.py:
  - memory_loader.py  → POST /hook/prompt → FastAPI → scorer.py → response
  - memory_loader_lc.py → build_memory_pipeline().invoke() → MemoryContext → format

Side effects preserved:
  - Writes current_prompt_keywords.tmp (used by PostToolUse hook)
  - Writes current_prompt_text.tmp (used by stop_hook)
"""
import json
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
from sqlite_log_handler import setup
from utils import read_stdin, write_json_to_stdout

from langchain_learning.pipeline import build_memory_pipeline, MemoryContext

log = setup("memory_loader_lc")

_VAULT_INDEX_DB  = _cfg.icloud_db_dir / "vault_index.sqlite"
_PROMPT_KW_TMP   = Path.home() / ".claude/current_prompt_keywords.tmp"
_PROMPT_TEXT_TMP = Path.home() / ".claude/current_prompt_text.tmp"
_PROMPT_ID_TMP   = _cfg.prompt_id_tmp


# ---------------------------------------------------------------------------
# Pipeline singleton — built once per hook process, reused across calls
# (in practice hooks are short-lived; singleton avoids rebuild on retry)
# ---------------------------------------------------------------------------
_pipeline = None


def _get_pipeline():
    global _pipeline
    if _pipeline is None:
        _pipeline = build_memory_pipeline(use_llm=False)
    return _pipeline


# ---------------------------------------------------------------------------
# Prompt extraction — mirrors PromptHandler.extract_prompt()
# ---------------------------------------------------------------------------

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
    return prompt


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

def _format_system_prompt(ctx: MemoryContext) -> str:
    """Convert MemoryContext into the injected system prompt block.

    Sections (all conditional on non-empty data):
      # Active domains      — detected domain tags
      ## Injected memories  — scored memories from MEMORY.sqlite
      ## Suggested tools    — top tool hints from tool_hints.sqlite
      ## Session context    — top-2 session_summaries snippets by keyword score
    """
    lines: list[str] = []

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

    if ctx.get("session_context"):
        lines.append("## Session context")
        lines.append(ctx["session_context"])
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

        # generate prompt_id for gate tracking — pre_tool_use + tool_usage_logger read this
        prompt_id = str(uuid.uuid4())
        _PROMPT_ID_TMP.write_text(prompt_id)
        log.debug("prompt_id generated: %s", prompt_id)

        # LangChain pipeline — replaces HTTP POST to FastAPI
        pipeline = _get_pipeline()
        ctx: MemoryContext = pipeline.invoke({"prompt": prompt, "cwd": cwd})

        system_prompt = _format_system_prompt(ctx)

        log.info(
            "lc hook: domains=%s memories=%d tools=%d session_snapshot_ids=%s",
            ctx["domains"], len(ctx["memories"]), len(ctx["tool_hints"]),
            ctx.get("session_context_ids", []),
        )

        if system_prompt:
            write_json_to_stdout({"hookSpecificOutput": {"additionalSystemPrompt": system_prompt}})
        else:
            write_json_to_stdout()

    except Exception as e:
        log.error("memory_loader_lc failed: %s", e)
        write_json_to_stdout(error=f"memory_loader_lc failed: {e}")


if __name__ == "__main__":
    main()
