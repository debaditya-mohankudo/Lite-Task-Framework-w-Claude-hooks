"""LogTaskEventsNode — appends a turn event to the active task at session stop.

Also auto-detects task completion via keyword matching on the last prompt summary.
If completion keywords are found, flips status to 'done' and clears active_task_id
from state so the next session starts clean.
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_PROMPT_TMP = Path.home() / ".claude" / "current_prompt_text.tmp"
_TASK_ACTIVATE_SCRIPT = Path.home() / "workspace/claude-hooks/scripts/task_activate.py"

_MAX_SUMMARY = 200

# Phrases that signal the task is complete. Matched against the full prompt text
# (not truncated summary) so longer sentences like "all tests passing" are caught.
# Primary signal: "task:<id> done" convention — explicit and unambiguous
_TASK_DONE_PATTERN = re.compile(r"\btask:[a-f0-9]{6,}\s+done\b", re.IGNORECASE)

# Secondary heuristics — broader but kept as fallback
_COMPLETION_PATTERNS = re.compile(
    r"("
    r"\bdone[.!]?\s*$|"
    r"\b(?:mark(?:ed|ing)?(?:\s+as)?|task(?:s)?(?:\s+is|\s+are)?)\s+done\b|"
    r"\b(?:completed?|finished?|fixed)\b.{0,40}|"
    r"\ball tests?\s+passing\b|"
    r"\btask(?:s)?\s+complete\b|"
    r"\b(?:it|now|that|this)\s+works?[.!]"
    r")",
    re.IGNORECASE,
)


def _is_completion_signal(text: str) -> bool:
    return bool(_TASK_DONE_PATTERN.search(text) or _COMPLETION_PATTERNS.search(text))


def _prompt_summary() -> tuple[str, str]:
    """Return (full_text, truncated_summary). Deletes tmp file after read."""
    try:
        text = _PROMPT_TMP.read_text().strip()
        _PROMPT_TMP.unlink(missing_ok=True)
        return text, text[:_MAX_SUMMARY]
    except Exception:
        return "", ""


def _clear_checkpoint(session_id: str) -> None:
    """Zero active_task_id in LangGraph checkpoint via the task_activate script."""
    try:
        subprocess.run(
            ["uv", "run", "python", str(_TASK_ACTIVATE_SCRIPT), "clear", session_id],
            capture_output=True, timeout=15,
            cwd=str(_TASK_ACTIVATE_SCRIPT.parent.parent),
        )
    except Exception as exc:
        _log.warning("[log_task_events] checkpoint clear failed: %s", exc)


class LogTaskEventsNode:
    """Write one task_event row for the active task at session stop.

    Reads active_task_id directly from checkpoint state — set explicitly by
    tasks__set_active, never guessed. No-ops silently when no task is active.

    Auto-completion detection: if the last prompt text contains completion
    keywords, marks the task done and clears the checkpoint so the next
    session starts without a stale active task.
    """

    def __call__(self, state: SessionState) -> dict:
        entry("log_task_events", state)

        task_id = state.get("active_task_id", "")
        if not task_id:
            _log.info("[log_task_events] no active task — skipping")
            return {}

        if not _cfg.tasks_db.exists():
            _log.warning("[log_task_events] proj_tasks.db not found")
            return {}

        prompt_id  = state.get("prompt_id", "")
        session_id = state.get("session_id", "")
        turn       = state.get("turn", 0)
        tools_raw  = state.get("prompt_tools") or []
        tools_str  = ",".join(t["tool"] if isinstance(t, dict) else str(t) for t in tools_raw)
        full_text, summary = _prompt_summary()

        auto_completed = _is_completion_signal(full_text)

        try:
            with sqlite3.connect(str(_cfg.tasks_db), timeout=5) as conn:
                conn.execute(
                    """INSERT INTO task_events
                       (task_id, prompt_id, session_id, turn, summary, tools)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (task_id, prompt_id, session_id, turn, summary, tools_str),
                )
                if auto_completed:
                    conn.execute(
                        "UPDATE open_tasks SET status='done', updated_at=datetime('now') WHERE id=?",
                        (task_id,),
                    )
                    _log.info("[log_task_events] auto-completed task=%s", task_id)
                else:
                    conn.execute(
                        "UPDATE open_tasks SET updated_at=datetime('now') WHERE id=?",
                        (task_id,),
                    )
            _log.info("[log_task_events] logged task=%s turn=%d tools=%s auto_completed=%s",
                      task_id, turn, tools_str, auto_completed)
        except Exception as exc:
            _log.error("[log_task_events] DB error: %s", exc)
            return {}

        if auto_completed and session_id:
            _clear_checkpoint(session_id)
            return {"active_task_id": "", "active_task_title": "", "task_memories": [], "task_context": []}

        return {}
