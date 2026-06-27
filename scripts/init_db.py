"""One-time setup script — create all SQLite databases and apply migrations.

Run once after cloning or when a new database needs to be initialized:
    uv run python scripts/init_db.py

Safe to re-run — all CREATE TABLE statements use IF NOT EXISTS and migrations
check PRAGMA table_info before altering.
"""
from __future__ import annotations

import sqlite3

from src.config import config as _cfg
from src.db.schema import (
    MEMORIES_DDL,
    MCP_TOOL_HINTS_DDL,
    OPEN_TASKS_DDL,
    TASK_EDGES_DDL,
    TASK_EVENTS_DDL,
    HOOK_LOGS_DDL,
    TEST_RUNS_DDL,
    migrate_memory_db,
    migrate_tasks_db,
    migrate_tool_hints_db,
    migrate_hooks_db,
)


def init_memory_db() -> None:
    _cfg.memory_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(_cfg.memory_db)) as conn:
        conn.executescript(MEMORIES_DDL)
        migrate_memory_db(conn)
    print(f"  ✓ {_cfg.memory_db}")


def init_tasks_db() -> None:
    _cfg.tasks_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(_cfg.tasks_db)) as conn:
        conn.executescript(OPEN_TASKS_DDL)
        conn.executescript(TASK_EVENTS_DDL)
        conn.executescript(TASK_EDGES_DDL)
        migrate_tasks_db(conn)
    print(f"  ✓ {_cfg.tasks_db}")


def init_tool_hints_db() -> None:
    _cfg.tool_hints_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(_cfg.tool_hints_db)) as conn:
        conn.executescript(MCP_TOOL_HINTS_DDL)
        migrate_tool_hints_db(conn)
    print(f"  ✓ {_cfg.tool_hints_db}")


def init_hooks_db() -> None:
    _cfg.log_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(_cfg.log_db)) as conn:
        conn.executescript(HOOK_LOGS_DDL)
        conn.executescript(TEST_RUNS_DDL)
        migrate_hooks_db(conn)
    print(f"  ✓ {_cfg.log_db}")


if __name__ == "__main__":
    print("Initializing databases...")
    init_memory_db()
    init_tasks_db()
    init_tool_hints_db()
    init_hooks_db()
    print("Done.")
