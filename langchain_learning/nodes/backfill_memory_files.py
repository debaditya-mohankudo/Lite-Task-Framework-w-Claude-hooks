"""BackfillMemoryFilesNode — default BackfillNodeProtocol implementation.

Reads task_files + active_task_domain from state (emitted by ActivateTaskNode)
and updates memories whose tags overlap with file stem tokens, backfilling the
files column for records where it is currently NULL.

This is the default node occupying the single backfill slot in the UPS graph.
To swap: replace the graph edge — no subclassing required.
See: langchain_learning/nodes/base.py BackfillNodeProtocol for the contract.

Tags: memory, backfill, files, post-tool-use, pluggable
"""
from __future__ import annotations

import re
import sqlite3

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.nodes._text_utils import tokenise
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_TEST_SESSION_PREFIXES = ("test-", "pytest-", "api-test-", "replay-")


def _parse_files_section(body: str) -> list[str]:
    """Extract file paths from the 'Files:' section of a task body."""
    m = re.search(r"^Files:\s*\n(.*?)(?=\n[A-Z][a-z]+:|\Z)", body, re.MULTILINE | re.DOTALL)
    if not m:
        return []
    raw = m.group(1).strip()
    return [p.strip() for p in re.split(r"[,\n]+", raw) if p.strip()]


def _file_tokens(paths: list[str]) -> set[str]:
    """Turn file stems into match tokens. hooks/gates.py → {gate, gates}.

    Only stems are used — directory names (e.g. 'hooks') are too generic
    and would match memory slugs containing 'claude-hooks' incorrectly.
    """
    tokens: set[str] = set()
    for path in paths:
        stem = re.sub(r"\.[a-z]+$", "", path.split("/")[-1])
        parts = re.split(r"[_\-]", stem)
        for part in parts:
            t = part.lower()
            if len(t) >= 3:
                tokens.add(t)
                if t.endswith("s") and len(t) > 4:
                    tokens.add(t[:-1])
                else:
                    tokens.add(t + "s")
    return tokens


def _run_backfill(domain: str, file_paths: list[str]) -> int:
    """Write files column for NULL-files memories whose tags overlap with file tokens."""
    if not domain or not _cfg.memory_db.exists():
        return 0

    file_tok = _file_tokens(file_paths)
    if not file_tok:
        return 0

    files_value = ", ".join(file_paths)

    try:
        with sqlite3.connect(str(_cfg.memory_db), timeout=5) as conn:
            rows = conn.execute(
                """
                SELECT name, tags FROM memories
                WHERE files IS NULL AND domain = ?
                ORDER BY COALESCE(last_validated, updated) ASC
                LIMIT 5
                """,
                (domain,),
            ).fetchall()

            updated = 0
            for name, tags in rows:
                mem_tok = tokenise(f"{name} {tags or ''}")
                if mem_tok & file_tok:
                    conn.execute(
                        "UPDATE memories SET files = ? WHERE name = ?",
                        (files_value, name),
                    )
                    _log.info(
                        "[backfill_memory_files] backfilled memory=%s domain=%s files=%r",
                        name, domain, files_value,
                    )
                    updated += 1
        return updated
    except Exception as exc:
        _log.warning("[backfill_memory_files] error domain=%s: %s", domain, exc)
        return 0


class BackfillMemoryFilesNode:
    """Default BackfillNodeProtocol node — token-overlap strategy.

    Reads task_files + active_task_domain from state. Skipped for replay/test
    sessions. Emits backfill_count.
    """

    def __call__(self, state: SessionState) -> dict:
        entry("backfill_memory_files", state)

        session_id = str(state.get("session_id", ""))
        if any(session_id.startswith(p) for p in _TEST_SESSION_PREFIXES):
            return {"backfill_count": 0}

        task_files: list[str] = state.get("task_files") or []
        domain: str = state.get("active_task_domain") or ""

        if not task_files or not domain:
            return {"backfill_count": 0}

        count = _run_backfill(domain, task_files)
        if count:
            _log.info("[backfill_memory_files] backfilled %d memories domain=%s", count, domain)
        return {"backfill_count": count}
