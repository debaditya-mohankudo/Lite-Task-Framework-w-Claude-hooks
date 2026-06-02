"""Shared logger — writes to the SQLite hook_logs table, falls back to stderr.

Logger names use the `lc.<module>` prefix so they're easy to filter separately
from hook logs in the hook_logs table.

Usage:
    from src.logger import get_logger
    _log = get_logger(__name__)   # → "lc.langchain_learning.memory_retriever"
"""
import logging
import sqlite3


_SCHEMA = """
CREATE TABLE IF NOT EXISTS hook_logs (
    id      INTEGER PRIMARY KEY,
    logger  TEXT      NOT NULL,
    level   TEXT      NOT NULL,
    message TEXT      NOT NULL,
    ts      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""
_UNSET = object()


class _SQLiteHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self._db_path = _UNSET  # _UNSET = not yet resolved, None = permanently failed

    def _ensure_db(self) -> str | None:
        if self._db_path is _UNSET:
            try:
                from langchain_learning.config import config as _cfg
                path = str(_cfg.log_db)
                with sqlite3.connect(path) as conn:
                    conn.executescript(_SCHEMA)
                self._db_path = path
            except Exception:
                self._db_path = None  # cache failure — don't retry
        return self._db_path

    def emit(self, record: logging.LogRecord) -> None:
        db = self._ensure_db()
        if not db:
            return
        try:
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "INSERT INTO hook_logs (logger, level, message) VALUES (?, ?, ?)",
                    (record.name, record.levelname, self.format(record)),
                )
        except Exception:
            pass

    def handleError(self, record: logging.LogRecord) -> None:
        pass


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """Return a logger prefixed with 'lc.' backed by SQLite, with stderr fallback."""
    lc_name = f"lc.{name}" if not name.startswith("lc.") else name
    logger = logging.getLogger(lc_name)
    if not logger.handlers:
        logger.addHandler(_SQLiteHandler())
        logger.addHandler(logging.StreamHandler())
    logger.setLevel(level)
    return logger
