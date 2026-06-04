"""Shared logger — buffers records in-process, flushes atomically via flush_logs().

Logger names use the `lc.<module>` prefix so they're easy to filter separately
from hook logs in the hook_logs table.

Usage:
    from src.logger import get_logger, flush_logs
    _log = get_logger(__name__)   # → "lc.langchain_learning.memory_retriever"
    flush_logs()                  # call once at hook exit — single executemany commit
"""
import logging
import sqlite3
from datetime import datetime, timezone


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

# Module-level buffer — all _SQLiteHandler instances append here
_buffer: list[tuple[str, str, str, str]] = []
_db_path: object = _UNSET  # shared resolved path


def _ensure_db() -> str | None:
    global _db_path
    if _db_path is _UNSET:
        try:
            from langchain_learning.config import config as _cfg
            path = str(_cfg.log_db)
            with sqlite3.connect(path) as conn:
                conn.executescript(_SCHEMA)
            _db_path = path
        except Exception:
            _db_path = None
    return _db_path


class _SQLiteHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        _buffer.append((record.name, record.levelname, self.format(record), ts))

    def handleError(self, record: logging.LogRecord) -> None:
        pass


def flush_logs() -> None:
    """Write all buffered log records to SQLite in one commit, then clear the buffer."""
    if not _buffer:
        return
    db = _ensure_db()
    if not db:
        _buffer.clear()
        return
    rows = list(_buffer)
    _buffer.clear()
    try:
        with sqlite3.connect(db) as conn:
            conn.executemany(
                "INSERT INTO hook_logs (logger, level, message, ts) VALUES (?, ?, ?, ?)",
                rows,
            )
    except Exception:
        pass


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """Return a logger prefixed with 'lc.' backed by the shared buffer, with stderr fallback."""
    lc_name = f"lc.{name}" if not name.startswith("lc.") else name
    logger = logging.getLogger(lc_name)
    if not logger.handlers:
        logger.addHandler(_SQLiteHandler())
    logger.setLevel(level)
    return logger
