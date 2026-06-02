"""Shared logger for langchain_learning — writes to the same SQLite DB as hooks.

Logger names use the `lc.<module>` prefix so they're easy to filter separately
from hook logs in the hook_logs table.

Usage:
    from langchain_learning.logger import get_logger
    _log = get_logger(__name__)   # → "lc.langchain_learning.memory_retriever"
"""
import logging
import sqlite3

from langchain_learning.config import config as _cfg


_SCHEMA = """
CREATE TABLE IF NOT EXISTS hook_logs (
    id      INTEGER PRIMARY KEY,
    logger  TEXT      NOT NULL,
    level   TEXT      NOT NULL,
    message TEXT      NOT NULL,
    ts      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class _SQLiteHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self._db_path = str(_cfg.log_db)
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.executescript(_SCHEMA)
        except Exception:
            pass

    def emit(self, record: logging.LogRecord) -> None:
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO hook_logs (logger, level, message) VALUES (?, ?, ?)",
                    (record.name, record.levelname, self.format(record)),
                )
        except Exception:
            pass

    def handleError(self, record: logging.LogRecord) -> None:
        pass


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """Return a logger prefixed with 'lc.' and backed by the shared SQLite DB."""
    lc_name = f"lc.{name}" if not name.startswith("lc.") else name
    logger = logging.getLogger(lc_name)
    if not any(isinstance(h, _SQLiteHandler) for h in logger.handlers):
        logger.addHandler(_SQLiteHandler())
    logger.setLevel(level)
    return logger
