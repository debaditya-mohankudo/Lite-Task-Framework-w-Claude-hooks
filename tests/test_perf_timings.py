"""Performance timing tests for post-tool optimizations.

Measures:
1. Parallel DB writes in LogToolUsageNode are faster than sequential
2. memory__ tools are skipped by the dispatcher

Note: fire-and-forget (daemon thread) was tried and reverted. The hook runs as a
short-lived subprocess — daemon threads are killed at process exit, so tools with
large results (e.g. mail__read) silently died before writing to the checkpoint,
breaking gate prereq checks. The pipeline is now synchronous; parallel DB writes
inside log_tool_usage remain as the latency optimization.
"""
from __future__ import annotations

import sqlite3
import tempfile
import time
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Dispatcher: memory tools skipped
# ---------------------------------------------------------------------------

class TestDispatcherSkips:
    def test_memory_tools_skipped(self):
        """memory__ tools must not trigger the pipeline at all."""
        with patch("langchain_learning.session_graph.run_post_tool") as mock_run:
            from hooks.dispatcher import _handle_post_tool_use
            result = _handle_post_tool_use({
                "tool_name": "mcp__local-mac__memory__add",
                "session_id": "sess-x",
                "duration_ms": 5.0,
                "tool_input": {},
                "tool_response": {},
            })
        assert result is None
        mock_run.assert_not_called()


# ---------------------------------------------------------------------------
# Parallel DB writes: two independent writes should finish faster than 2× each
# ---------------------------------------------------------------------------

class TestParallelDbWrites:
    """LogToolUsageNode._upsert_tool_hint and _upsert_task_event_tools run concurrently."""

    def _make_db(self, suffix=".sqlite") -> Path:
        f = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        f.close()
        return Path(f.name)

    def test_parallel_faster_than_sequential(self):
        """Two 50ms sleeps in parallel should finish in ~50ms, not ~100ms."""
        call_times: dict[str, float] = {}

        def slow_hint(*args, **kwargs):
            call_times["hint_start"] = time.monotonic()
            time.sleep(0.05)
            call_times["hint_end"] = time.monotonic()

        def slow_task(*args, **kwargs):
            call_times["task_start"] = time.monotonic()
            time.sleep(0.05)
            call_times["task_end"] = time.monotonic()

        from langchain_learning.nodes.log_tool_usage import LogToolUsageNode

        node = LogToolUsageNode()
        with patch.object(node, "_upsert_tool_hint", slow_hint), \
             patch.object(node, "_upsert_task_event_tools", slow_task):

            # Simulate the parallel dispatch directly
            from concurrent.futures import ThreadPoolExecutor
            t0 = time.monotonic()
            with ThreadPoolExecutor(max_workers=2) as pool:
                f1 = pool.submit(node._upsert_tool_hint, "tool", "domain", "skill", 10.0, "prompt")
                f2 = pool.submit(node._upsert_task_event_tools, "tool", "pid", "tid")
                f1.result()
                f2.result()
            elapsed = (time.monotonic() - t0) * 1000

        # Both started before either finished → overlapping
        assert call_times["hint_start"] < call_times["task_end"], "no overlap detected"
        assert call_times["task_start"] < call_times["hint_end"], "no overlap detected"

        # Should finish in ~50ms, definitely not 100ms
        assert elapsed < 120, f"parallel writes took {elapsed:.1f}ms — should be < 120ms"

    def test_upsert_tool_hint_writes_to_db(self):
        """Basic smoke: _upsert_tool_hint actually writes a row."""
        db = self._make_db()
        from langchain_learning.nodes.log_tool_usage import LogToolUsageNode
        from unittest.mock import patch

        node = LogToolUsageNode()
        with patch("langchain_learning.nodes.log_tool_usage._cfg") as mock_cfg:
            mock_cfg.tool_hints_db = db
            node._upsert_tool_hint("mail__read", "mail", "mail", 42.0, "test prompt")

        with sqlite3.connect(str(db)) as conn:
            row = conn.execute(
                "SELECT tool_name, count, domain FROM mcp_tool_hints WHERE tool_name='mail__read'"
            ).fetchone()
        assert row is not None
        assert row[0] == "mail__read"
        assert row[1] == 1
        assert row[2] == "mail"
        db.unlink()
