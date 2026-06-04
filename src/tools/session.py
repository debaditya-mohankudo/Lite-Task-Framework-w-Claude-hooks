"""MCP tools for session state — direct SQLite access to sessions.db."""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

_DB = Path.home() / ".claude" / "sessions.db"


def _connect(read_only: bool = False) -> sqlite3.Connection:
    uri = f"file:{_DB}?mode=ro" if read_only else str(_DB)
    conn = sqlite3.connect(uri, uri=read_only, timeout=5)
    conn.row_factory = sqlite3.Row
    return conn


def _session_row(row: sqlite3.Row) -> dict:
    return {
        "session_id": row["session_id"],
        "turn":       row["turn"],
        "prompt_id":  row["prompt_id"] or "",
        "updated_at": row["updated_at"],
    }


def handle_list() -> list:
    """List all sessions with turn count and updated_at."""
    if not _DB.exists():
        return []
    try:
        with _connect(read_only=True) as conn:
            rows = conn.execute(
                "SELECT session_id, turn, prompt_id, updated_at FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [_session_row(r) for r in rows]
    except Exception as e:
        return [{"error": str(e)}]


def handle_list_all() -> list:
    """List all sessions from the SQLite DB."""
    return handle_list()


def handle_list_ids() -> list:
    """List all sessions with minimal fields only: session_id, turn, updated_at.

    Use this instead of session__list when you only need to identify sessions.
    """
    if not _DB.exists():
        return []
    try:
        with _connect(read_only=True) as conn:
            rows = conn.execute(
                "SELECT session_id, turn, updated_at FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [
            {
                "session_id": r["session_id"],
                "turn":       r["turn"],
                "updated_at": r["updated_at"],
            }
            for r in rows
        ]
    except Exception as e:
        return [{"error": str(e)}]


def handle_get(session_id: str) -> dict:
    """Get session data for a given session_id.

    Args:
        session_id: The Claude Code session UUID.
    """
    if not _DB.exists():
        return {"error": "sessions.db not found"}
    try:
        with _connect(read_only=True) as conn:
            row = conn.execute(
                "SELECT session_id, turn, prompt_id, updated_at FROM sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row is None:
            return {"error": f"session {session_id!r} not found"}
        return _session_row(row)
    except Exception as e:
        return {"error": str(e)}


def handle_delete(session_id: str) -> dict:
    """Delete a session by ID from the SQLite DB.

    Args:
        session_id: The Claude Code session UUID to delete.
    """
    if not _DB.exists():
        return {"error": "sessions.db not found"}
    try:
        with _connect() as conn:
            conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
            conn.commit()
        return {"ok": True, "deleted": session_id}
    except Exception as e:
        return {"error": str(e)}


def handle_save_summary(
    session_id: str,
    summary: str,
    tags: list[str] | None = None,
    turn_at: int = 0,
) -> dict:
    """Save a compact conceptual summary snapshot for the session.

    Args:
        session_id: The Claude Code session UUID.
        summary: 3-6 sentence capture of ideas, decisions, and concepts from this session.
        tags: Short concept labels (e.g. ['session-design', 'fastapi', 'memory-architecture']).
        turn_at: The turn number at the time of the snapshot.
    """
    if not _DB.exists():
        return {"error": "sessions.db not found"}
    tags_str = ",".join(tags) if tags else ""
    try:
        with _connect() as conn:
            cur = conn.execute(
                """INSERT INTO session_summaries (session_id, summary, tags, turn_at)
                   VALUES (?, ?, ?, ?)""",
                (session_id, summary, tags_str, turn_at),
            )
            conn.commit()
            return {"ok": True, "id": cur.lastrowid}
    except Exception as e:
        return {"error": str(e)}


def handle_delete_summary(session_id: str, summary_id: int) -> dict:
    """Delete a summary snapshot by its ID.

    Args:
        session_id: The Claude Code session UUID.
        summary_id: The integer ID of the summary row to delete.
    """
    if not _DB.exists():
        return {"error": "sessions.db not found"}
    try:
        with _connect() as conn:
            conn.execute(
                "DELETE FROM session_summaries WHERE id = ? AND session_id = ?",
                (summary_id, session_id),
            )
            conn.commit()
        return {"ok": True, "deleted": summary_id}
    except Exception as e:
        return {"error": str(e)}


def handle_get_summaries(session_id: str) -> list:
    """Get all summary snapshots for a session, ordered by creation time.

    Args:
        session_id: The Claude Code session UUID.
    """
    if not _DB.exists():
        return []
    try:
        with _connect(read_only=True) as conn:
            rows = conn.execute(
                """SELECT id, session_id, summary, tags, turn_at, created_at
                   FROM session_summaries WHERE session_id = ?
                   ORDER BY created_at ASC""",
                (session_id,),
            ).fetchall()
        return [
            {
                "id":         r["id"],
                "session_id": r["session_id"],
                "summary":    r["summary"],
                "tags":       [t.strip() for t in (r["tags"] or "").split(",") if t.strip()],
                "turn_at":    r["turn_at"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    except Exception as e:
        return [{"error": str(e)}]


def handle_search(query: str, top_k: int = 5, session_id: str | None = None) -> list:
    """Search session summary snapshots by keyword relevance across all sessions.

    Tags are weighted 3x over summary body text. Returns top matching snapshots with scores.
    Use this to recall prior context, coding patterns, decisions, or lessons learned.

    Args:
        query: Space-separated keywords to search for (e.g. 'python oop singleton hooks').
        top_k: Maximum number of results to return (default 5).
        session_id: Optional — restrict search to a specific session UUID.
    """
    if not _DB.exists():
        return []
    keywords = {t for t in re.findall(r"[a-z]{3,}", query.lower()) if t}
    if not keywords:
        return []
    try:
        with _connect(read_only=True) as conn:
            sql = "SELECT id, session_id, summary, tags, turn_at, created_at FROM session_summaries"
            params: tuple = ()
            if session_id:
                sql += " WHERE session_id = ?"
                params = (session_id,)
            rows = conn.execute(sql, params).fetchall()
    except Exception as e:
        return [{"error": str(e)}]

    def _score(row: sqlite3.Row) -> int:
        tag_hits  = sum(3 for t in (row["tags"] or "").split(",") if t.strip() in keywords)
        body_hits = sum(1 for w in (row["summary"] or "").lower().split() if w.strip(".,;:") in keywords)
        return tag_hits + body_hits

    scored = sorted(rows, key=_score, reverse=True)
    results = []
    for row in scored[:top_k]:
        s = _score(row)
        if s == 0:
            break
        results.append({
            "id":         row["id"],
            "session_id": row["session_id"],
            "summary":    row["summary"],
            "tags":       [t.strip() for t in (row["tags"] or "").split(",") if t.strip()],
            "turn_at":    row["turn_at"],
            "created_at": row["created_at"],
            "score":      s,
        })
    return results


def handle_persist(session_id: str) -> dict:
    """No-op kept for API compatibility.

    Args:
        session_id: The Claude Code session UUID.
    """
    return {"ok": True, "session_id": session_id}
