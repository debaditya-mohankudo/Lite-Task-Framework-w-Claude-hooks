"""Shared text utilities for node scoring."""
from __future__ import annotations

import re
import sqlite3
from typing import Optional


def tokenise(text: str) -> set[str]:
    """Return set of lowercase tokens (3+ chars) from text."""
    return {t for t in re.findall(r"[a-z]{3,}", text.lower()) if t}


def task_project_tag(task_id: str, tasks_db) -> Optional[str]:
    """Return the project:<name> tag value for task_id from proj_tasks.db, or None."""
    if not tasks_db.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{tasks_db}?mode=ro", uri=True)
        row = conn.execute("SELECT tags FROM open_tasks WHERE id = ?", (task_id,)).fetchone()
        conn.close()
        if row is None:
            return None
        for tag in (row[0] or "").split(","):
            tag = tag.strip()
            if tag.startswith("project:"):
                return tag[len("project:"):]
    except Exception:
        pass
    return None
