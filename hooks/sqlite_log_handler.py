#!/usr/bin/env python3
"""
SQLiteHandler — logging.Handler that persists records to iCloud SQLite.

Usage:
    from sqlite_log_handler import setup
    from utils import write_json_to_stdout
    log = setup("memory_loader")

    log.error("something went wrong: %s", e)   # → hook_logs table
    write_json_to_stdout()                      # → {} to harness
    write_json_to_stdout(error="reason")        # → surfaces reason to Claude
"""
import logging
import sqlite3

from src.config import config as _cfg
LOG_DB_PATH = _cfg.log_db
from utils import write_json_to_stdout as write_json_to_stdout  # re-export

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hook_logs (
    id      INTEGER PRIMARY KEY,
    logger  TEXT    NOT NULL,
    level   TEXT    NOT NULL,
    message TEXT    NOT NULL,
    ts      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class SQLiteHandler(logging.Handler):
    """Writes log records to a SQLite table. Never raises."""

    def __init__(self, db_path=LOG_DB_PATH):
        super().__init__()
        self._db_path = db_path
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.executescript(_SCHEMA)
        except Exception:
            pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO hook_logs (logger, level, message) VALUES (?, ?, ?)",
                    (record.name, record.levelname, msg),
                )
        except Exception:
            pass  # handleError would re-raise; we want silence

    def handleError(self, record: logging.LogRecord) -> None:
        pass  # suppress — logging must never crash a hook


def setup(name: str, level: int = logging.INFO) -> logging.Logger:
    """Return a logger named `name` with SQLiteHandler attached."""
    logger = logging.getLogger(name)
    if not any(isinstance(h, SQLiteHandler) for h in logger.handlers):
        logger.addHandler(SQLiteHandler())
    logger.setLevel(level)
    return logger
