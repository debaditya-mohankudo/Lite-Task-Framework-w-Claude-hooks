"""Shared pytest configuration — ensures all test files have the project root
and hooks/ directory on sys.path, matching the runtime environment."""
import sys
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent
for _p in [str(_PROJECT_ROOT), str(_PROJECT_ROOT / "hooks")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

_TEST_LOG_DB = _PROJECT_ROOT / "tests" / "test_logs.db"

# Counters for test_runs summary — populated by pytest_runtest_logreport
_run_counts: dict[str, int] = {"n_tests": 0, "n_passed": 0, "n_failed": 0}



@pytest.fixture(scope="session", autouse=True)
def test_log_db():
    """Redirect all SQLiteHandler writes to tests/test_logs.db for the test run.

    DB accumulates across runs — each run is tagged with a unique run_id.
    Query with run_id = (SELECT MAX(run_id) FROM test_runs) to scope to latest run.
    """
    import src.logger as _logger

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    _run_counts["n_tests"] = 0
    _run_counts["n_passed"] = 0
    _run_counts["n_failed"] = 0

    # Get the existing singleton BEFORE patching _LOG_DB, then redirect it.
    # Order matters: instance() uses _LOG_DB as the dict key.
    h = _logger.SQLiteHandler.instance()
    h.redirect(str(_TEST_LOG_DB))
    _logger._LOG_DB = _TEST_LOG_DB

    # run_id is test-only — add column + trigger so every INSERT is auto-tagged.
    # Prod code never touches run_id; the trigger handles it transparently.
    with sqlite3.connect(str(_TEST_LOG_DB)) as conn:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(hook_logs)")}
        if "run_id" not in cols:
            conn.execute("ALTER TABLE hook_logs ADD COLUMN run_id TEXT")
        conn.execute("CREATE TABLE IF NOT EXISTS _run_meta (run_id TEXT)")
        conn.execute("DELETE FROM _run_meta")
        conn.execute("INSERT INTO _run_meta VALUES (?)", (run_id,))
        conn.execute("DROP TRIGGER IF EXISTS _tag_run_id")
        conn.execute("""
            CREATE TRIGGER _tag_run_id AFTER INSERT ON hook_logs
            BEGIN
                UPDATE hook_logs SET run_id = (SELECT run_id FROM _run_meta LIMIT 1)
                WHERE id = NEW.id;
            END
        """)

    yield _TEST_LOG_DB

    # Write test_runs summary row at session end
    try:
        with sqlite3.connect(str(_TEST_LOG_DB)) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO test_runs (run_id, n_tests, n_passed, n_failed) VALUES (?, ?, ?, ?)",
                (run_id, _run_counts["n_tests"], _run_counts["n_passed"], _run_counts["n_failed"]),
            )
    except Exception:
        pass


def pytest_runtest_logreport(report):
    """Accumulate pass/fail counts for the test_runs summary row."""
    if report.when != "call":
        return
    _run_counts["n_tests"] += 1
    if report.passed:
        _run_counts["n_passed"] += 1
    elif report.failed or report.outcome == "error":
        _run_counts["n_failed"] += 1


@pytest.fixture(scope="function", autouse=True)
def _log_test_marker(request, test_log_db):
    """Write TEST_START sentinel before each test.

    Yields a scoped query callable — use it instead of query_test_logs()
    when you want rows scoped to this test only.

    Usage:
        def test_something(_log_test_marker):
            ...
            rows = _log_test_marker(logger="lc.hooks.gates", search="name_arg_check")
    """
    import src.logger as _logger

    sentinel_logger = _logger.setup("pytest.marker", level=10)  # DEBUG
    sentinel_logger.info("[TEST_START] %s", request.node.nodeid)

    # Record the id of the sentinel row so we can scope queries to this test
    with sqlite3.connect(str(_TEST_LOG_DB)) as conn:
        start_id = conn.execute("SELECT MAX(id) FROM hook_logs").fetchone()[0] or 0

    def _query(logger: str = "", search: str | list[str] = "", level: str = "") -> list[dict]:
        """Query logs written during this test only (after the TEST_START sentinel)."""
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


@pytest.fixture
def log_turn():
    """Return a callable that writes a [TURN_START] sentinel, marking a turn boundary within a test.

    Usage:
        def test_multi_turn(mem_graph, log_turn):
            log_turn("turn 1")
            sg.run_session("prompt 1", ...)
            log_turn("turn 2")
            sg.run_session("prompt 2", ...)
    """
    import src.logger as _logger

    sentinel_logger = _logger.setup("pytest.marker", level=10)

    def _mark(label: str = "") -> None:
        sentinel_logger.info("[TURN_START] %s", label)

    return _mark


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
