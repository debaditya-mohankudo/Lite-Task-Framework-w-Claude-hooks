"""MCP tools for task relations — typed edges between tasks in proj_tasks.db."""
from __future__ import annotations

import sqlite3
from pathlib import Path

_DB = Path.home() / ".claude" / "proj_tasks.db"

_RELATION_TYPES = {"related_to", "duplicate_of", "caused_by", "blocks", "blocked_by"}

# Symmetric pairs: storing A→B also writes B→inverse automatically
_INVERSE: dict[str, str | None] = {
    "blocks":       "blocked_by",
    "blocked_by":   "blocks",
    "caused_by":    None,
    "duplicate_of": None,
    "related_to":   None,
}


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_edges (
            from_id       TEXT NOT NULL,
            to_id         TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            created_at    TIMESTAMP DEFAULT (datetime('now')),
            PRIMARY KEY (from_id, to_id, relation_type)
        )
    """)
    conn.commit()
    return conn


def handle_relate(from_id: str, to_id: str, relation_type: str) -> dict:
    """Add a typed relation edge between two tasks.

    Stores one canonical edge. Symmetric inverses (blocks ↔ blocked_by) are
    written automatically. Both tasks must exist in proj_tasks.db.

    Args:
        from_id:       Source task id.
        to_id:         Target task id.
        relation_type: One of: related_to, duplicate_of, caused_by, blocks, blocked_by.
    """
    if relation_type not in _RELATION_TYPES:
        return {"ok": False, "error": f"unknown relation_type '{relation_type}'. Valid: {sorted(_RELATION_TYPES)}"}
    if not _DB.exists():
        return {"ok": False, "error": "proj_tasks.db not found"}
    with _connect() as conn:
        for tid in (from_id, to_id):
            if not conn.execute("SELECT id FROM open_tasks WHERE id=?", (tid,)).fetchone():
                return {"ok": False, "error": f"task not found: {tid}"}
        conn.execute(
            "INSERT OR REPLACE INTO task_edges (from_id, to_id, relation_type) VALUES (?,?,?)",
            (from_id, to_id, relation_type),
        )
        inverse = _INVERSE.get(relation_type)
        if inverse:
            conn.execute(
                "INSERT OR REPLACE INTO task_edges (from_id, to_id, relation_type) VALUES (?,?,?)",
                (to_id, from_id, inverse),
            )
        conn.commit()
    return {"ok": True, "from_id": from_id, "to_id": to_id, "relation_type": relation_type}


def handle_unrelate(from_id: str, to_id: str, relation_type: str = "") -> dict:
    """Remove a relation edge (and its automatic inverse, if any).

    Args:
        from_id:       Source task id.
        to_id:         Target task id.
        relation_type: Specific type to remove. If empty, removes all edges between the pair.
    """
    if not _DB.exists():
        return {"ok": False, "error": "proj_tasks.db not found"}
    with _connect() as conn:
        if relation_type:
            conn.execute(
                "DELETE FROM task_edges WHERE from_id=? AND to_id=? AND relation_type=?",
                (from_id, to_id, relation_type),
            )
            inverse = _INVERSE.get(relation_type)
            if inverse:
                conn.execute(
                    "DELETE FROM task_edges WHERE from_id=? AND to_id=? AND relation_type=?",
                    (to_id, from_id, inverse),
                )
        else:
            conn.execute(
                "DELETE FROM task_edges WHERE (from_id=? AND to_id=?) OR (from_id=? AND to_id=?)",
                (from_id, to_id, to_id, from_id),
            )
        conn.commit()
    return {"ok": True}


def handle_neighbors(task_id: str) -> list:
    """Return all relation edges touching task_id (both directions).

    Each entry: task_id (neighbour), relation_type, direction (outgoing/incoming),
    title, status.

    Args:
        task_id: Task to query.
    """
    if not _DB.exists():
        return []
    with _connect() as conn:
        rows = conn.execute("""
            SELECT e.to_id AS neighbour, e.relation_type, 'outgoing' AS direction,
                   t.title, t.status
            FROM task_edges e JOIN open_tasks t ON t.id = e.to_id
            WHERE e.from_id = ?
            UNION
            SELECT e.from_id AS neighbour, e.relation_type, 'incoming' AS direction,
                   t.title, t.status
            FROM task_edges e JOIN open_tasks t ON t.id = e.from_id
            WHERE e.to_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM task_edges e2
                  WHERE e2.from_id = ? AND e2.to_id = e.from_id
              )
            ORDER BY relation_type, direction
        """, (task_id, task_id, task_id)).fetchall()
    return [dict(r) for r in rows]
