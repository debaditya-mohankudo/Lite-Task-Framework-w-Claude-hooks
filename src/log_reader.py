"""Read rows from the central hook_logs SQLite table.

Single responsibility: query claude_hooks.sqlite, return plain dicts.
No logging, no handler setup — import from src.logger for those.

Usage:
    from src.log_reader import read_logs

    read_logs()
    read_logs(logger="memory_loader_lc", level="INFO", since="30m")
    read_logs(logger="pipeline", level="WARNING", since="24h", limit=20)
"""
from __future__ import annotations

import sqlite3

from langchain_learning.config import config as _cfg

_SINCE_MAP = {
    "30m":  "-30 minutes",
    "1h":   "-1 hour",
    "6h":   "-6 hours",
    "24h":  "-24 hours",
    "today": "start of day",
}


def read_logs(
    logger: str | None = None,
    level: str | None = None,
    since: str = "1h",
    limit: int = 100,
) -> list[dict]:
    """Return recent hook_log rows as dicts, newest first.

    Args:
        logger: substring match on logger name (e.g. "memory_loader_lc")
        level:  exact level filter — "DEBUG", "INFO", "WARNING", "ERROR"
        since:  time window — "30m", "1h", "6h", "24h", "today"
        limit:  max rows returned
    """
    db = str(_cfg.log_db)
    if not db:
        return []

    interval = _SINCE_MAP.get(since, "-1 hour")
    clauses: list[str] = [f"ts >= datetime('now', '{interval}')"]
    params: list = []

    if logger:
        clauses.append("logger LIKE ?")
        params.append(f"%{logger}%")
    if level:
        clauses.append("level = ?")
        params.append(level.upper())

    where = " AND ".join(clauses)
    sql = (
        f"SELECT id, logger, level, message, ts FROM hook_logs"
        f" WHERE {where} ORDER BY id DESC LIMIT ?"
    )
    params.append(limit)

    try:
        with sqlite3.connect(db) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, params).fetchall()]
    except Exception:
        return []
