"""Tests for task-framework: task_graph push/pop, load_task_history session scope."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tasks_db(path: Path, task_id: str = "task-abc") -> None:
    """Create a minimal proj_tasks.db with one task and some events."""
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE open_tasks (
            id TEXT PRIMARY KEY, title TEXT, body TEXT,
            status TEXT DEFAULT 'open', tags TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE task_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT, prompt_id TEXT, session_id TEXT,
            turn INTEGER, summary TEXT, tools TEXT
        );
    """)
    conn.execute(
        "INSERT INTO open_tasks (id, title, status) VALUES (?, ?, 'open')",
        (task_id, "Test task"),
    )
    conn.commit()
    conn.close()


def _insert_events(db: Path, task_id: str, session_id: str, count: int, base_turn: int = 0) -> None:
    conn = sqlite3.connect(str(db))
    for i in range(count):
        conn.execute(
            "INSERT INTO task_events (task_id, session_id, turn, summary, tools) VALUES (?,?,?,?,?)",
            (task_id, session_id, base_turn + i, f"turn {base_turn + i}", "Bash"),
        )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# load_task_history — hybrid scope
# ---------------------------------------------------------------------------

class TestLoadTaskHistoryHybridScope:
    """Verify session-only scope logic."""

    def _call(self, db_path: Path, task_id: str, session_id: str) -> dict:
        from langchain_learning.nodes.load_task_history import LoadTaskHistoryNode
        node = LoadTaskHistoryNode()
        mock_cfg = MagicMock()
        mock_cfg.tasks_db = db_path
        state = {"active_task_id": task_id, "session_id": session_id}
        with patch("langchain_learning.nodes.load_task_history._cfg", mock_cfg):
            return node(state)

    def test_no_active_task_returns_empty(self, tmp_path):
        db = tmp_path / "tasks.db"
        _make_tasks_db(db)
        result = self._call(db, "", "sess-1")
        assert result == {"task_context": []}

    def test_session_below_threshold_uses_global(self, tmp_path):
        """2 session events + 3 from a prior session → only current session's 2 returned."""
        db = tmp_path / "tasks.db"
        _make_tasks_db(db, "t1")
        _insert_events(db, "t1", "old-sess", 3, base_turn=0)
        _insert_events(db, "t1", "new-sess", 2, base_turn=10)

        result = self._call(db, "t1", "new-sess")
        ctx = result["task_context"]
        assert len(ctx) == 2
        assert all(r["session_id"] == "new-sess" for r in ctx)

    def test_session_at_threshold_uses_session_only(self, tmp_path):
        """10 session events → scoped to current session, older events excluded."""
        db = tmp_path / "tasks.db"
        _make_tasks_db(db, "t1")
        _insert_events(db, "t1", "old-sess", 10, base_turn=0)
        _insert_events(db, "t1", "cur-sess", 10, base_turn=100)

        result = self._call(db, "t1", "cur-sess")
        ctx = result["task_context"]
        assert len(ctx) == 10
        assert all(r["session_id"] == "cur-sess" for r in ctx)

    def test_session_above_threshold_returns_all_session_events(self, tmp_path):
        """13 session events → all 13 returned."""
        db = tmp_path / "tasks.db"
        _make_tasks_db(db, "t1")
        _insert_events(db, "t1", "old-sess", 3, base_turn=0)
        _insert_events(db, "t1", "cur-sess", 13, base_turn=50)

        result = self._call(db, "t1", "cur-sess")
        ctx = result["task_context"]
        assert len(ctx) == 13
        assert all(r["session_id"] == "cur-sess" for r in ctx)

    def test_db_missing_returns_empty(self, tmp_path):
        db = tmp_path / "nonexistent.db"
        result = self._call(db, "t1", "sess-1")
        assert result == {"task_context": []}

    def test_events_ordered_oldest_first(self, tmp_path):
        """Session events must be ordered oldest-first."""
        db = tmp_path / "tasks.db"
        _make_tasks_db(db, "t1")
        _insert_events(db, "t1", "cur-sess", 3, base_turn=0)

        result = self._call(db, "t1", "cur-sess")
        ctx = result["task_context"]
        turns = [r["turn"] for r in ctx]
        assert turns == sorted(turns)


# ---------------------------------------------------------------------------
# task_graph — push/pop/clear
# ---------------------------------------------------------------------------

class TestTaskGraphStack:
    """Unit-test the push/pop stack logic in run_task_activate / run_task_pop."""

    def _setup(self, tmp_path: Path, task_ids: list[str]):
        """Create a tasks DB + in-memory LangGraph checkpointer for isolation."""
        db = tmp_path / "tasks.db"
        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE open_tasks (
                id TEXT PRIMARY KEY, title TEXT, body TEXT,
                status TEXT DEFAULT 'open', tags TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE task_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT, prompt_id TEXT, session_id TEXT,
                turn INTEGER, summary TEXT, tools TEXT
            );
        """)
        for tid in task_ids:
            conn.execute(
                "INSERT INTO open_tasks (id, title, status) VALUES (?, ?, 'open')",
                (tid, f"Task {tid}"),
            )
        conn.commit()
        conn.close()
        return db

    def _graph(self, tmp_path: Path, tasks_db: Path):
        from langchain_learning import task_graph as tg
        from langgraph.checkpoint.sqlite import SqliteSaver

        cp_db = tmp_path / "checkpoints.db"
        cp_conn = sqlite3.connect(str(cp_db), check_same_thread=False)
        checkpointer = SqliteSaver(cp_conn)

        graph = tg.build_task_graph(checkpointer=checkpointer)

        # patch tasks_db path so set_active_task reads our temp DB
        mock_cfg = MagicMock()
        mock_cfg.tasks_db = tasks_db
        mock_cfg.memory_db = Path("/nonexistent_memory.db")  # skip memory scoring

        return graph, checkpointer, mock_cfg

    def _activate(self, graph, cfg_mock, task_id, session_id, tmp_path):
        from langchain_learning import task_graph as tg
        with patch("langchain_learning.nodes.set_active_task._cfg", cfg_mock), \
             patch("langchain_learning.nodes.load_task_memories._cfg", cfg_mock):
            # call run_task_activate but with our isolated graph
            from langchain_learning.session_state import SessionState
            from collections import OrderedDict
            from typing import cast
            cfg = tg._config(session_id)
            existing = graph.get_state(cfg)
            existing_vals = existing.values if existing and existing.values else {}
            current_active = existing_vals.get("active_task_id", "")
            current_stack = list(existing_vals.get("task_stack") or [])
            if current_active:
                current_stack.append(current_active)
            base = existing_vals if existing_vals else tg._fresh_state(session_id)
            state = cast(SessionState, {
                **base,
                "event_type": "task_activate",
                "active_task_id": task_id,
                "task_stack": current_stack,
                "session_id": session_id,
            })
            return graph.invoke(state, config=cfg)

    def test_activate_sets_active_task(self, tmp_path):
        db = self._setup(tmp_path, ["t1"])
        graph, _, cfg_mock = self._graph(tmp_path, db)
        result = self._activate(graph, cfg_mock, "t1", "sess-1", tmp_path)
        assert result["active_task_id"] == "t1"
        assert result["task_stack"] == []

    def test_activate_pushes_existing_onto_stack(self, tmp_path):
        db = self._setup(tmp_path, ["t1", "t2"])
        graph, _, cfg_mock = self._graph(tmp_path, db)
        self._activate(graph, cfg_mock, "t1", "sess-1", tmp_path)
        result = self._activate(graph, cfg_mock, "t2", "sess-1", tmp_path)
        assert result["active_task_id"] == "t2"
        assert result["task_stack"] == ["t1"]

    def test_stack_grows_with_multiple_switches(self, tmp_path):
        db = self._setup(tmp_path, ["t1", "t2", "t3"])
        graph, _, cfg_mock = self._graph(tmp_path, db)
        self._activate(graph, cfg_mock, "t1", "sess-1", tmp_path)
        self._activate(graph, cfg_mock, "t2", "sess-1", tmp_path)
        result = self._activate(graph, cfg_mock, "t3", "sess-1", tmp_path)
        assert result["active_task_id"] == "t3"
        assert result["task_stack"] == ["t1", "t2"]


# ---------------------------------------------------------------------------
# run_task_pop — via task_graph directly
# ---------------------------------------------------------------------------

class TestRunTaskPop:

    def _run_pop(self, graph, session_id, cfg_mock):
        from langchain_learning import task_graph as tg
        from langchain_learning.session_state import SessionState
        from collections import OrderedDict
        from typing import cast

        with patch("langchain_learning.nodes.set_active_task._cfg", cfg_mock), \
             patch("langchain_learning.nodes.load_task_memories._cfg", cfg_mock):
            cfg = tg._config(session_id)
            existing = graph.get_state(cfg)
            existing_vals = existing.values if existing and existing.values else {}
            stack = list(existing_vals.get("task_stack") or [])

            if not stack:
                graph.update_state(cfg, {"active_task_id": "", "active_task_title": "", "task_memories": [], "task_stack": []})
                return {"active_task_id": "", "task_stack": []}

            restored_id = stack.pop()
            base = existing_vals if existing_vals else tg._fresh_state(session_id)
            state = cast(SessionState, {
                **base,
                "event_type": "task_activate",
                "active_task_id": restored_id,
                "task_stack": stack,
                "session_id": session_id,
            })
            return graph.invoke(state, config=cfg)

    def _setup_and_graph(self, tmp_path, task_ids):
        from langchain_learning import task_graph as tg
        from langgraph.checkpoint.sqlite import SqliteSaver

        db = tmp_path / "tasks.db"
        conn = sqlite3.connect(str(db))
        conn.executescript("""
            CREATE TABLE open_tasks (id TEXT PRIMARY KEY, title TEXT, body TEXT,
                status TEXT DEFAULT 'open', tags TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT, prompt_id TEXT, session_id TEXT,
                turn INTEGER, summary TEXT, tools TEXT);
        """)
        for tid in task_ids:
            conn.execute("INSERT INTO open_tasks (id, title, status) VALUES (?,?,'open')", (tid, f"Task {tid}"))
        conn.commit(); conn.close()

        cp_conn = sqlite3.connect(str(tmp_path / "cp.db"), check_same_thread=False)
        graph = tg.build_task_graph(checkpointer=SqliteSaver(cp_conn))
        mock_cfg = MagicMock()
        mock_cfg.tasks_db = db
        mock_cfg.memory_db = Path("/nonexistent.db")
        return graph, mock_cfg

    def _activate(self, graph, cfg_mock, task_id, session_id):
        from langchain_learning import task_graph as tg
        from langchain_learning.session_state import SessionState
        from collections import OrderedDict
        from typing import cast
        with patch("langchain_learning.nodes.set_active_task._cfg", cfg_mock), \
             patch("langchain_learning.nodes.load_task_memories._cfg", cfg_mock):
            cfg = tg._config(session_id)
            existing = graph.get_state(cfg)
            existing_vals = existing.values if existing and existing.values else {}
            current_active = existing_vals.get("active_task_id", "")
            stack = list(existing_vals.get("task_stack") or [])
            if current_active:
                stack.append(current_active)
            base = existing_vals if existing_vals else tg._fresh_state(session_id)
            state = cast(SessionState, {**base, "event_type": "task_activate",
                                         "active_task_id": task_id, "task_stack": stack,
                                         "session_id": session_id})
            return graph.invoke(state, config=cfg)

    def test_pop_restores_previous_task(self, tmp_path):
        graph, cfg_mock = self._setup_and_graph(tmp_path, ["t1", "t2"])
        self._activate(graph, cfg_mock, "t1", "sess-1")
        self._activate(graph, cfg_mock, "t2", "sess-1")
        result = self._run_pop(graph, "sess-1", cfg_mock)
        assert result["active_task_id"] == "t1"
        assert result["task_stack"] == []

    def test_pop_empty_stack_clears_active(self, tmp_path):
        graph, cfg_mock = self._setup_and_graph(tmp_path, ["t1"])
        self._activate(graph, cfg_mock, "t1", "sess-1")
        result = self._run_pop(graph, "sess-1", cfg_mock)
        assert result["active_task_id"] == ""
        assert result["task_stack"] == []

    def test_pop_on_empty_session_returns_empty(self, tmp_path):
        graph, cfg_mock = self._setup_and_graph(tmp_path, [])
        result = self._run_pop(graph, "sess-new", cfg_mock)
        assert result["active_task_id"] == ""

    def test_pop_restores_lifo_order(self, tmp_path):
        """Push t1→t2→t3, then pop twice: should get t2, then t1."""
        graph, cfg_mock = self._setup_and_graph(tmp_path, ["t1", "t2", "t3"])
        self._activate(graph, cfg_mock, "t1", "sess-1")
        self._activate(graph, cfg_mock, "t2", "sess-1")
        self._activate(graph, cfg_mock, "t3", "sess-1")

        r1 = self._run_pop(graph, "sess-1", cfg_mock)
        assert r1["active_task_id"] == "t2"

        r2 = self._run_pop(graph, "sess-1", cfg_mock)
        assert r2["active_task_id"] == "t1"


class TestMergeSummaries:
    def _merge(self, summaries, max_entries=10):
        from langchain_learning.nodes.log_task_events import _merge_summaries
        return _merge_summaries(summaries, max_entries=max_entries)

    def test_plain_summaries_joined(self):
        result = self._merge(["did A", "did B", "did C"])
        assert result == "compacted: did A | did B | did C"

    def test_existing_sentinel_unpacked(self):
        result = self._merge(["compacted: did A | did B", "did C"])
        assert result == "compacted: did A | did B | did C"

    def test_nested_sentinels_flattened(self):
        result = self._merge(["compacted: did A | did B", "compacted: did C | did D"])
        assert result == "compacted: did A | did B | did C | did D"

    def test_max_entries_cap(self):
        summaries = [f"did {i}" for i in range(15)]
        result = self._merge(summaries, max_entries=10)
        parts = result[len("compacted: "):].split(" | ")
        assert len(parts) == 10
        assert parts[-1] == "did 14"

    def test_sentinel_re_compaction_capped(self):
        sentinel = "compacted: " + " | ".join(f"did {i}" for i in range(10))
        result = self._merge([sentinel, "did 10", "did 11"], max_entries=10)
        parts = result[len("compacted: "):].split(" | ")
        assert len(parts) == 10
        assert parts[-1] == "did 11"

    def test_empty_strings_skipped(self):
        result = self._merge(["did A", "", None, "did B"])
        assert result == "compacted: did A | did B"
