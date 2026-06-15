"""Shared pytest configuration — ensures all test files have the project root
and hooks/ directory on sys.path, matching the runtime environment."""
import sys
import sqlite3
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent
for _p in [str(_PROJECT_ROOT), str(_PROJECT_ROOT / "hooks")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TEST_LOG_DB = _PROJECT_ROOT / "tests" / "test_logs.db"


@pytest.fixture(scope="session", autouse=True)
def test_log_db():
    """Redirect all lc.* buffered log writes to tests/test_logs.db for the test run.

    The DB persists after the run — query it with the same patterns as
    mcp__claude-hooks__hooks__read_logs_sqlite for post-hoc log inspection.
    """
    import src.logger as _logger

    _TEST_LOG_DB.unlink(missing_ok=True)
    _logger._LOG_DB = _TEST_LOG_DB
    _logger._db_path_resolved = _logger._DB_UNSET
    _logger._buffer.clear()

    yield _TEST_LOG_DB


@pytest.fixture(scope="function", autouse=True)
def _log_test_marker(request, test_log_db):
    """Write TEST_START sentinel, flush before and after each test.

    Yields a scoped query callable — use it instead of query_test_logs()
    when you want rows scoped to this test only.

    Usage:
        def test_something(_log_test_marker):
            ...
            rows = _log_test_marker(logger="lc.hooks.gates", search="name_arg_check")
    """
    import src.logger as _logger

    _logger._buffer.append(("pytest.marker", "INFO", f"[TEST_START] {request.node.nodeid}", ""))
    _logger.flush_logs()

    # Record the id of the sentinel row so we can scope queries to this test
    with sqlite3.connect(str(_TEST_LOG_DB)) as conn:
        start_id = conn.execute("SELECT MAX(id) FROM hook_logs").fetchone()[0] or 0

    def _query(logger: str = "", search: str | list[str] = "", level: str = "") -> list[dict]:
        """Query logs written during this test only (after the TEST_START sentinel)."""
        _logger.flush_logs()
        clauses = ["id > ?"]
        params: list = [start_id]
        if logger:
            clauses.append("logger LIKE ?")
            params.append(f"%{logger}%")
        searches = [search] if isinstance(search, str) else search
        for s in searches:
            if s:
                clauses.append("message LIKE ?")
                params.append(f"%{s}%")
        if level:
            clauses.append("level = ?")
            params.append(level.upper())
        where = f"WHERE {' AND '.join(clauses)}"
        sql = f"SELECT id, ts, level, logger, message FROM hook_logs {where} ORDER BY id"
        with sqlite3.connect(str(_TEST_LOG_DB)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    yield _query
    _logger.flush_logs()


def query_test_logs(logger: str = "", search: str | list[str] = "", level: str = "", limit: int = 200) -> list[dict]:
    """Query the full tests/test_logs.db (all tests in this run).

    For scoped queries within a single test, use the _log_test_marker fixture instead.
    """
    if not _TEST_LOG_DB.exists():
        return []
    clauses, params = [], []
    if logger:
        clauses.append("logger LIKE ?")
        params.append(f"%{logger}%")
    searches = [search] if isinstance(search, str) else search
    for s in searches:
        if s:
            clauses.append("message LIKE ?")
            params.append(f"%{s}%")
    if level:
        clauses.append("level = ?")
        params.append(level.upper())
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT id, ts, level, logger, message FROM hook_logs {where} ORDER BY id DESC LIMIT {limit}"
    with sqlite3.connect(str(_TEST_LOG_DB)) as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
