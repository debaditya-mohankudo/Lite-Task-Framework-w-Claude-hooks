"""SummarizeTaskContextNode — compress active task context via BareClaudeAgent before injection.

Runs after the 4-loader fan-in (load_task_history, load_task_code, load_related_tasks,
load_related_commits) and before the second fan-out (cwd_domain_detect, load_memories,
score_tools). Skipped when total raw context < 800 chars.

On success, sets task_context_summary; dispatcher injects that instead of the raw lists.
Falls back silently (leaves task_context_summary empty) on timeout or error.

Tags: task-context, summarize, subagent, compression, context-injection
"""
from __future__ import annotations

import textwrap
import threading

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from langchain_learning.subagent import BareClaudeAgent
from src.logger import get_logger

_log = get_logger(__name__)

_THRESHOLD_CHARS = 800
_TIMEOUT_SECONDS = 6

_SYSTEM_PROMPT = textwrap.dedent("""\
    You are a context compressor for a coding assistant.
    Given prior task history, related code, and related past work, produce a concise
    bullet-point summary (max 200 words):
    - What has been done so far
    - Current state / where things stand
    - Key files and modules involved
    - Relevant patterns from related prior work
    Be dense — no filler, no repetition, no preamble.
""").strip()


def _build_raw_context(state: SessionState) -> str:
    parts: list[str] = []

    task_context = state.get("task_context") or []
    if task_context:
        lines = ["## Task history"]
        unique_sids = {ev.get("session_id", "") for ev in task_context}
        multi = len(unique_sids) > 1
        for ev in task_context:
            turn = ev.get("turn", "?")
            summary = ev.get("summary", "").strip()
            tools = ev.get("tools", "").strip()
            sid = (ev.get("session_id") or "")[:8]
            line = f"- [{sid}] turn {turn}" if multi else f"- turn {turn}"
            if summary:
                line += f": {summary}"
            if tools:
                line += f" [{tools}]"
            lines.append(line)
        parts.append("\n".join(lines))

    rag_chunks = state.get("task_rag_chunks") or []
    if rag_chunks:
        lines = ["## Relevant code"]
        for c in rag_chunks:
            name = c.get("name", "")
            mod = c.get("module", "?")
            file = c.get("file", "")
            line_no = c.get("line", "")
            label = f"`{name}`" if name else f"`{mod}`"
            loc = f"{file}:{line_no}" if line_no else file
            lines.append(f"- {label} — {loc}")
        parts.append("\n".join(lines))

    related_tasks = state.get("related_tasks") or []
    if related_tasks:
        lines = ["## Related past tasks"]
        for t in related_tasks:
            lines.append(f"- {t['id']}: {t['title']}")
            if t.get("body_snippet"):
                lines.append(f"  {t['body_snippet']}")
        parts.append("\n".join(lines))

    related_commits = state.get("related_commits") or []
    if related_commits:
        lines = ["## Related commits"]
        for c in related_commits:
            commit = c.get("commit_hash", "?")
            file = c.get("file", "")
            score = c.get("score", 0)
            lines.append(f"- `{commit}` {file} [{score:.3f}]")
        parts.append("\n".join(lines))

    return "\n\n".join(parts)


class SummarizeTaskContextNode:
    """Compress task_context + related_* + rag_chunks into a single tight summary via claude -p.

    Skipped when no active task or total raw context < 800 chars.
    Falls back to empty string (raw lists used) on timeout or subprocess error.

    Tags: summarize-task-context, subagent, compression, haiku, bare-agent
    """

    def __call__(self, state: SessionState) -> dict:
        entry("summarize_task_context", state)

        if not state.get("active_task_id"):
            return {"task_context_summary": ""}

        raw = _build_raw_context(state)
        if len(raw) < _THRESHOLD_CHARS:
            _log.info("[summarize_task_context] raw=%d chars < threshold — skipping", len(raw))
            return {"task_context_summary": ""}

        _log.info("[summarize_task_context] raw=%d chars — invoking BareClaudeAgent", len(raw))

        prompt = f"Summarize the following task context:\n\n{raw}"

        try:
            agent = BareClaudeAgent(system_prompt=_SYSTEM_PROMPT)

            result: list[str] = []
            error: list[Exception] = []

            def _run() -> None:
                try:
                    result.append(agent.invoke(prompt))
                except Exception as exc:
                    error.append(exc)

            t = threading.Thread(target=_run, daemon=True)
            t.start()
            t.join(timeout=_TIMEOUT_SECONDS)

            if t.is_alive():
                _log.warning("[summarize_task_context] timeout after %ds — falling back", _TIMEOUT_SECONDS)
                return {"task_context_summary": ""}
            if error:
                _log.warning("[summarize_task_context] error: %s — falling back", error[0])
                return {"task_context_summary": ""}

            summary = result[0].strip()
            _log.info("[summarize_task_context] summary=%d chars", len(summary))
            return {"task_context_summary": summary}

        except Exception as exc:
            _log.warning("[summarize_task_context] error: %s — falling back", exc)
            return {"task_context_summary": ""}
