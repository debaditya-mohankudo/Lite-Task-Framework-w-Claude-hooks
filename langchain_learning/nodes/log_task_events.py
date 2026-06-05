"""LogTaskEventsNode — appends a turn event to each task pinned in the last prompt."""
from __future__ import annotations

import sqlite3
from pathlib import Path

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_TASKS_DB   = Path.home() / ".claude" / "proj_tasks.db"
_PROMPT_TMP = Path.home() / ".claude" / "current_prompt_text.tmp"

_MAX_SUMMARY = 200


def _prompt_summary() -> str:
    """First _MAX_SUMMARY chars of the last prompt text, stripped."""
    try:
        text = _PROMPT_TMP.read_text().strip()
        return text[:_MAX_SUMMARY]
    except Exception:
        return ""


class LogTaskEventsNode:
    """Write one task_event row per task that was pinned (task:<id>) in the last prompt.

    Reads open_tasks from checkpoint state — those are the tasks load_open_tasks
    scored/pinned. Only tasks with id explicitly referenced in the prompt are
    logged (score >= 2.0 means pinned; we check presence in state instead of
    re-parsing the prompt to avoid duplication).

    Uses prompt_id, session_id, turn from state for traceability.
    Tools are read from prompt_tools (list of tool short-names this turn).
    """

    def __call__(self, state: SessionState) -> dict:
        entry("log_task_events", state)

        pinned = [t for t in (state.get("open_tasks") or []) if t.get("_pinned")]
        if not pinned:
            _log.info("[log_task_events] no pinned tasks — skipping")
            return {}

        if not _TASKS_DB.exists():
            _log.warning("[log_task_events] proj_tasks.db not found")
            return {}

        prompt_id  = state.get("prompt_id", "")
        session_id = state.get("session_id", "")
        turn       = state.get("turn", 0)
        tools_raw  = state.get("prompt_tools") or []
        tools_str  = ",".join(str(t) for t in tools_raw) if tools_raw else ""
        summary    = _prompt_summary()

        try:
            with sqlite3.connect(str(_TASKS_DB), timeout=5) as conn:
                for task in pinned:
                    task_id = task.get("id", "")
                    if not task_id:
                        continue
                    conn.execute(
                        """INSERT INTO task_events
                           (task_id, prompt_id, session_id, turn, summary, tools)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (task_id, prompt_id, session_id, turn, summary, tools_str),
                    )
                    conn.execute(
                        "UPDATE open_tasks SET updated_at=datetime('now') WHERE id=?",
                        (task_id,),
                    )
            _log.info("[log_task_events] logged turn=%d for %d tasks", turn, len(pinned))
        except Exception as exc:
            _log.error("[log_task_events] DB error: %s", exc)

        return {}
