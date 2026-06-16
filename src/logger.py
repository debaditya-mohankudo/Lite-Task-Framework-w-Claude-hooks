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
import threading

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
    """Singleton per db_path — opens a fresh connection on each emit."""

    _instances: dict[str, "SQLiteHandler"] = {}

    def __new__(cls, db_path=None):
        key = str(db_path or _LOG_DB)
        if key not in cls._instances:
            instance = super().__new__(cls)
            cls._instances[key] = instance
        return cls._instances[key]

    def __init__(self, db_path=None):
        if hasattr(self, "_initialized"):
            return
        super().__init__()
        self._db_path = str(db_path or _LOG_DB)
        self._lock = threading.Lock()
        self._initialized = True
        self._ensure_schema()

    @classmethod
    def instance(cls) -> "SQLiteHandler":
        """Return the default singleton (keyed on _LOG_DB)."""
        return cls()

    def _ensure_schema(self) -> None:
        """Create tables and apply any missing column migrations. Never raises."""
        try:
            with sqlite3.connect(self._db_path) as conn:
                conn.executescript(_SCHEMA)
                # Migration: add run_id if the table predates it
                cols = {row[1] for row in conn.execute("PRAGMA table_info(hook_logs)")}
                if "run_id" not in cols:
                    conn.execute("ALTER TABLE hook_logs ADD COLUMN run_id TEXT")
        except Exception:
            pass

    def redirect(self, db_path: str) -> None:
        """Redirect writes to a new path and re-ensure schema (used by conftest)."""
        with self._lock:
            self._db_path = db_path
            self._ensure_schema()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with self._lock:
                with sqlite3.connect(self._db_path) as conn:
                    conn.execute(
                        "INSERT INTO hook_logs (logger, level, message, run_id) VALUES (?, ?, ?, ?)",
                        (record.name, record.levelname, msg, _run_id),
                    )
                    count = conn.execute(
                        "SELECT COUNT(*) FROM hook_logs"
                    ).fetchone()[0]
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
    """Return a logger with the shared SQLiteHandler attached (immediate writes)."""
    logger = logging.getLogger(name)
    h = SQLiteHandler.instance()
    if h not in logger.handlers:
        logger.addHandler(h)
    logger.setLevel(level)
    return logger


def get_logger(name: str, level: int = logging.DEBUG) -> logging.Logger:
    """Return a logger prefixed with 'lc.' for the LCEL pipeline (immediate writes)."""
    lc_name = f"lc.{name}" if not name.startswith("lc.") else name
    logger = logging.getLogger(lc_name)
    h = SQLiteHandler.instance()
    if h not in logger.handlers:
        logger.addHandler(h)
    logger.setLevel(level)
    return logger
