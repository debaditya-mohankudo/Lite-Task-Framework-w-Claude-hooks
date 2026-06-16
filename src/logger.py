"""Shared logger for hooks and the LangChain pipeline.

Two usage patterns:

Hook scripts (immediate write):
    from src.logger import setup
    log = setup("my_hook")
    log.info("done")

LCEL pipeline (also immediate — FastAPI server is long-lived, no flush needed):
    from src.logger import get_logger
    _log = get_logger(__name__)   # → "lc.<name>"
"""
import logging
import sqlite3
from pathlib import Path

from src.config import config as _cfg

_LOG_DB = _cfg.log_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hook_logs (
    id      INTEGER PRIMARY KEY,
    logger  TEXT      NOT NULL,
    level   TEXT      NOT NULL,
    message TEXT      NOT NULL,
    ts      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    run_id  TEXT
);
CREATE TABLE IF NOT EXISTS test_runs (
    run_id   TEXT PRIMARY KEY,
    ts       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    n_tests  INTEGER NOT NULL DEFAULT 0,
    n_passed INTEGER NOT NULL DEFAULT 0,
    n_failed INTEGER NOT NULL DEFAULT 0
);
"""

_run_id: str | None = None  # set by conftest (test env) or left None (production)


class SQLiteHandler(logging.Handler):
    """Writes each record immediately to SQLite. Never raises."""

    def __init__(self, db_path=None):
        super().__init__()
        self._db_path = str(db_path or _LOG_DB)
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
                    "INSERT INTO hook_logs (logger, level, message, run_id) VALUES (?, ?, ?, ?)",
                    (record.name, record.levelname, msg, _run_id),
                )
                count = conn.execute("SELECT COUNT(*) FROM hook_logs").fetchone()[0]
                if count >= 50_000:
                    conn.execute(
                        "DELETE FROM hook_logs WHERE id NOT IN "
                        "(SELECT id FROM hook_logs ORDER BY ts DESC LIMIT 40000)"
                    )
        except Exception:
            pass

    def handleError(self, record: logging.LogRecord) -> None:
        pass


def setup(name: str, level: int = logging.INFO) -> logging.Logger:
    """Return a logger with SQLiteHandler attached (immediate writes)."""
    logger = logging.getLogger(name)
    if not any(isinstance(h, SQLiteHandler) for h in logger.handlers):
        logger.addHandler(SQLiteHandler())
    logger.setLevel(level)
    return logger


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """Return a logger prefixed with 'lc.' for the LCEL pipeline (immediate writes)."""
    lc_name = f"lc.{name}" if not name.startswith("lc.") else name
    logger = logging.getLogger(lc_name)
    if not any(isinstance(h, SQLiteHandler) for h in logger.handlers):
        logger.addHandler(SQLiteHandler())
    logger.setLevel(level)
    return logger
