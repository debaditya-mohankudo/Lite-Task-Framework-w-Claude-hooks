"""Shared logger for hooks and the LangChain pipeline.

Two usage patterns:

Hook scripts (immediate write, no flush needed):
    from src.logger import setup
    log = setup("my_hook")
    log.info("done")

LCEL pipeline (buffered, single atomic commit at hook exit):
    from src.logger import get_logger, flush_logs
    _log = get_logger(__name__)   # → "lc.<name>"
    flush_logs()                  # call once at hook exit
"""
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.config import config as _cfg

_LOG_DB = _cfg.log_db

_SCHEMA = """
CREATE TABLE IF NOT EXISTS hook_logs (
    id      INTEGER PRIMARY KEY,
    logger  TEXT      NOT NULL,
    level   TEXT      NOT NULL,
    message TEXT      NOT NULL,
    ts      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""

# --- immediate handler (used by hook scripts via setup()) ---

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
                    "INSERT INTO hook_logs (logger, level, message) VALUES (?, ?, ?)",
                    (record.name, record.levelname, msg),
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


# --- buffered handler (used by LCEL pipeline via get_logger() + flush_logs()) ---

_buffer: list[tuple[str, str, str, str]] = []
_db_path_resolved: object = None
_DB_UNSET = object()
_db_path_resolved = _DB_UNSET


def _ensure_db() -> str | None:
    global _db_path_resolved
    if _db_path_resolved is _DB_UNSET:
        try:
            path = str(_LOG_DB)
            with sqlite3.connect(path) as conn:
                conn.executescript(_SCHEMA)
            _db_path_resolved = path
        except Exception:
            _db_path_resolved = None
    return _db_path_resolved


class _SQLiteBufferedHandler(logging.Handler):
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
    try:
        with sqlite3.connect(db) as conn:
            conn.executemany(
                "INSERT INTO hook_logs (logger, level, message, ts) VALUES (?, ?, ?, ?)",
                rows,
            )
        _buffer.clear()
    except Exception as e:
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            Path(f"/tmp/{ts}.log").write_text(
                "\n".join(f"{r[3]} [{r[1]}] {r[0]}: {r[2]}" for r in rows)
                + f"\n\nflush_logs error: {e}\n"
            )
        except Exception:
            pass


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """Return a buffered logger prefixed with 'lc.' for the LCEL pipeline."""
    lc_name = f"lc.{name}" if not name.startswith("lc.") else name
    logger = logging.getLogger(lc_name)
    if not logger.handlers:
        logger.addHandler(_SQLiteBufferedHandler())
    logger.setLevel(level)
    return logger
