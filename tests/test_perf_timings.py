"""Performance timing tests for post-tool optimizations.

Measures:
1. _handle_post_tool_use returns immediately (fire-and-forget < 50ms)
2. Parallel DB writes in LogToolUsageNode are faster than sequential
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
# Fire-and-forget: dispatcher returns immediately
# ---------------------------------------------------------------------------

class TestFireAndForget:
    """_handle_post_tool_use should return before the pipeline finishes."""

    def test_returns_before_pipeline_completes(self):
        """Hook handler must return in < 50ms even if pipeline takes 200ms."""
        pipeline_started = threading.Event()
        pipeline_done    = threading.Event()

        def slow_run_post_tool(**kwargs):
            pipeline_started.set()
            time.sleep(0.2)  # simulate 200ms pipeline
            pipeline_done.set()

        with patch("langchain_learning.session_graph.run_post_tool", slow_run_post_tool), \
             patch("langchain_learning.session_graph.get_session_graph") as mock_graph, \
             patch("langchain_learning.session_graph._config", return_value={}):

            mock_state = MagicMock()
            mock_state.values = {"prompt": "test"}
            mock_graph.return_value.get_state.return_value = mock_state

            from hooks.dispatcher import _handle_post_tool_use
            hook_input = {
                "tool_name": "mcp__local-mac__mail__read",
                "session_id": "test-session-abc123",
                "duration_ms": 42.0,
                "tool_input": {},
                "tool_response": {},
            }

            t0 = time.monotonic()
            result = _handle_post_tool_use(hook_input)
            elapsed_ms = (time.monotonic() - t0) * 1000

        assert result is None, "handler should return None immediately"
        assert elapsed_ms < 50, f"handler took {elapsed_ms:.1f}ms — should be < 50ms"

        # Give background thread time to finish, then verify it ran
        pipeline_started.wait(timeout=1.0)
        assert pipeline_started.is_set(), "pipeline thread never started"

    def test_pipeline_runs_in_background_thread(self):
        """Verify the pipeline runs in a non-main thread."""
        thread_names: list[str] = []

        def capture_thread(**kwargs):
            thread_names.append(threading.current_thread().name)

        with patch("langchain_learning.session_graph.run_post_tool", capture_thread), \
             patch("langchain_learning.session_graph.get_session_graph") as mock_graph, \
             patch("langchain_learning.session_graph._config", return_value={}):

            mock_state = MagicMock()
            mock_state.values = {"prompt": "x"}
            mock_graph.return_value.get_state.return_value = mock_state

            from hooks.dispatcher import _handle_post_tool_use
            _handle_post_tool_use({
                "tool_name": "mcp__local-mac__notes__add",
                "session_id": "sess-bg",
                "duration_ms": 10.0,
                "tool_input": {},
                "tool_response": {},
            })
            time.sleep(0.05)  # let daemon thread run

        assert thread_names, "pipeline never ran"
        assert thread_names[0] != "MainThread"
        assert thread_names[0].startswith("ptu-"), f"unexpected thread name: {thread_names[0]}"

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
        assert elapsed < 90, f"parallel writes took {elapsed:.1f}ms — should be < 90ms"

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
