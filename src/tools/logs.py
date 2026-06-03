"""MCP tools for reading hook_logs from claude_hooks.sqlite."""
from __future__ import annotations

from src.log_reader import read_logs as _read_logs


def handle_read_logs(
    logger: str | None = None,
    level: str | None = None,
    since: str = "1h",
    limit: int = 100,
) -> list[dict]:
    """Read recent entries from the central hook_logs table.

    Args:
        logger: substring match on logger name (e.g. "memory_loader_lc", "pipeline")
        level:  exact level filter — "DEBUG", "INFO", "WARNING", "ERROR"
        since:  time window — "30m", "1h", "6h", "24h", "today"
        limit:  max rows returned (newest first)
    """
    return _read_logs(logger=logger, level=level, since=since, limit=limit)
