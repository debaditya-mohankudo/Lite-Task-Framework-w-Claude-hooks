"""LogTaskEventsNode — appends a turn event to the active task at UPS time.

Runs at the end of the UserPromptSubmit chain (after set_prompt_id). Reads the
prompt directly from state["prompt"] — no tmp file needed. Tools column is empty
at insert time; PostToolUse upserts tool names into it as tools fire.

Also auto-detects task completion via keyword matching on the prompt text.
If completion keywords are found, flips status to 'done', clears active_task_id
from state, and clears the checkpoint (active_task_id + task_stack) via the
task_activate script so the next session starts clean.
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

_TASK_ACTIVATE_SCRIPT = Path.home() / "workspace/claude-hooks/scripts/task_activate.py"

_MAX_SUMMARY = 200
_MAX_EVENTS = 10  # compact oldest rows when total exceeds this

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


def _merge_summaries(summaries: list[str], max_entries: int = 10) -> str:
    """Merge a list of summary strings into a single compacted sentinel.

    Existing compacted sentinels are unpacked before merging so entries don't
    nest. Only the last max_entries parts are kept to bound the sentinel size.
    """
    parts: list[str] = []
    for s in summaries:
        s = s or ""
        if s.startswith("compacted: "):
            parts.extend(p.strip() for p in s[len("compacted: "):].split("|") if p.strip())
        elif s:
            parts.append(s)
    return "compacted: " + " | ".join(parts[-max_entries:])


def _compact_task_events(conn: sqlite3.Connection, task_id: str) -> None:
    """If task_events for task_id exceeds _MAX_EVENTS, collapse the oldest rows.

    The oldest (count - _MAX_EVENTS) rows are merged into a single compacted row
    (turn=-1) and deleted. This keeps the injected context bounded at _MAX_EVENTS
    rows without losing the fact that earlier turns existed.
    """
    rows = conn.execute(
        "SELECT id, summary, tools FROM task_events WHERE task_id = ? ORDER BY rowid ASC",
        (task_id,),
    ).fetchall()
    excess = len(rows) - _MAX_EVENTS
    if excess <= 0:
        return

    to_compact = rows[:excess]
    ids_to_delete = [r[0] for r in to_compact]
    merged_summary = _merge_summaries([r[1] for r in to_compact])
    merged_tools = ",".join(
        t for r in to_compact for t in (r[2] or "").split(",") if t
    )

    conn.execute(
        """INSERT INTO task_events (task_id, prompt_id, session_id, turn, summary, tools)
           VALUES (?, '', '', -1, ?, ?)""",
        (task_id, merged_summary[:_MAX_SUMMARY], merged_tools),
    )
    conn.execute(
        f"DELETE FROM task_events WHERE id IN ({','.join('?' * len(ids_to_delete))})",
        ids_to_delete,
    )
    _log.info("[log_task_events] compacted %d old events for task=%s", excess, task_id)


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
    """Write one task_event row per UPS turn for the active task.

    Runs at the end of the UserPromptSubmit chain (after set_prompt_id).
    Reads prompt directly from state["prompt"] — no tmp file needed.
    Tools column is inserted as empty string; PostToolUse upserts tool names
    as they fire via the prompt_id FK.

    Auto-completion detection: if the prompt text contains completion keywords,
    marks the task done and clears the checkpoint so the next session starts clean.

    Tags: task-events, task-history, user-prompt-submit, auto-completion, task-logging
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
        full_text  = (state.get("prompt") or "").strip()
        summary    = full_text[:_MAX_SUMMARY]

        auto_completed = _is_completion_signal(full_text)

        try:
            with sqlite3.connect(str(_cfg.tasks_db), timeout=5) as conn:
                conn.execute(
                    """INSERT OR IGNORE INTO task_events
                       (task_id, prompt_id, session_id, turn, summary, tools)
                       VALUES (?, ?, ?, ?, ?, '')""",
                    (task_id, prompt_id, session_id, turn, summary),
                )
                _compact_task_events(conn, task_id)
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
            _log.info("[log_task_events] logged task=%s turn=%d prompt_id=%s auto_completed=%s",
                      task_id, turn, prompt_id[:8] if prompt_id else "?", auto_completed)
        except Exception as exc:
            _log.error("[log_task_events] DB error: %s", exc)
            return {}

        if auto_completed and session_id:
            _clear_checkpoint(session_id)
            return {"active_task_id": "", "active_task_title": "", "task_memories": [], "task_context": []}

        return {}
