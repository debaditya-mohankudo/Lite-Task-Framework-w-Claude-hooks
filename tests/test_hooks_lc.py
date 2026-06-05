"""Tests for hook → session_graph integration.

All hook modules now delegate to session_graph nodes. Tests patch
session_graph._CHECKPOINTS_DB to redirect the SqliteSaver checkpoint DB
to a tmp directory, giving each test an isolated checkpoint store.

Covered:
  - pre_tool_use_lc:      Gate deny/allow, memory-tool skip, fail-open
  - tool_usage_logger_lc: tool_hints upsert, session recording, skip conditions
  - stop_hook_lc:         stopword filtering, state transition, empty-session skip
"""
import json
import sqlite3
import sys
from io import StringIO
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "hooks"))

from core.db.session_db import SessionDB
import langchain_learning.session_graph as sg_mod


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def sessions_db(tmp_path):
    db = SessionDB.open(tmp_path / "sessions.db")
    return db


@pytest.fixture()
def tool_hints_db(tmp_path):
    path = tmp_path / "tool_hints.sqlite"
    with sqlite3.connect(str(path)) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mcp_tool_hints (
                tool_name TEXT PRIMARY KEY,
                domain TEXT,
                count INTEGER DEFAULT 0,
                last_used TIMESTAMP,
                avg_latency_ms REAL DEFAULT 0.0,
                keywords TEXT DEFAULT '',
                skill TEXT DEFAULT '',
                recent_prompts TEXT DEFAULT '[]'
            )
        """)
        conn.execute("CREATE TABLE IF NOT EXISTS stopwords (word TEXT PRIMARY KEY)")
        conn.commit()
    return path


# ---------------------------------------------------------------------------
# pre_tool_use_lc
# ---------------------------------------------------------------------------

class TestPreToolUseLc:
    """Gate enforcement — tests patch session_graph._CHECKPOINTS_DB."""

    def _run(self, hook_input: dict, sessions_db_path: Path, checkpoints_db_path: Path | None = None) -> dict:
        import hooks.pre_tool_use_lc as hook_mod
        sg_mod._graph = None
        cp_path = checkpoints_db_path or (sessions_db_path.parent / "checkpoints.db")

        with patch.object(sg_mod, "_CHECKPOINTS_DB", cp_path), \
             patch.object(sg_mod, "_SESSIONS_DB", sessions_db_path), \
             patch("sys.stdin", StringIO(json.dumps(hook_input))), \
             patch("sys.stdout", new_callable=StringIO) as mock_out:
            hook_mod.main()
            out = mock_out.getvalue().strip()

        sg_mod._graph = None
        return json.loads(out) if out else {}

    def test_non_mcp_tool_passes_through(self, tmp_path):
        result = self._run(
            {"tool_name": "Bash", "session_id": "s1", "tool_use_id": "p1"},
            tmp_path / "sessions.db"
        )
        assert result == {}

    def test_memory_tool_skipped(self, tmp_path):
        result = self._run(
            {"tool_name": "mcp__local-mac__memory__add", "session_id": "s1", "tool_use_id": "p1"},
            tmp_path / "sessions.db"
        )
        assert result == {}

    def test_gated_tool_denied_without_prereq(self, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        SessionDB.open(sessions_db_path)  # create DB
        result = self._run(
            {"tool_name": "mcp__local-mac__imessage__send", "session_id": "s1", "tool_use_id": "p1"},
            sessions_db_path
        )
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def test_gated_tool_allowed_after_prereq(self, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        cp_path = tmp_path / "checkpoints.db"

        # UserPromptSubmit — seeds checkpoint with prompt_id
        sg_mod._graph = None
        with patch.object(sg_mod, "_CHECKPOINTS_DB", cp_path), \
             patch.object(sg_mod, "_SESSIONS_DB", sessions_db_path):
            sg_mod.run_session(prompt="send message", session_id="sess-1", cwd="/tmp")
        sg_mod._graph = None

        # PostToolUse — simulate contacts__search completing (appends to prompt_tools in checkpoint)
        import hooks.tool_usage_logger_lc as tul_mod
        import langchain_learning.nodes.log_tool_usage as tn
        from langchain_learning.config import config as lc_cfg
        from src.config import config as src_cfg
        tool_hints_path = tmp_path / "tool_hints.sqlite"
        import sqlite3
        with sqlite3.connect(str(tool_hints_path)) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS mcp_tool_hints (tool_name TEXT PRIMARY KEY, domain TEXT, count INTEGER DEFAULT 0, last_used TIMESTAMP, avg_latency_ms REAL DEFAULT 0.0, keywords TEXT DEFAULT '', skill TEXT DEFAULT '', recent_prompts TEXT DEFAULT '[]')")
            conn.execute("CREATE TABLE IF NOT EXISTS stopwords (word TEXT PRIMARY KEY)")
            conn.commit()
        mock_cfg = MagicMock()
        mock_cfg.tool_hints_db = tool_hints_path
        mock_cfg.prompt_id_tmp = src_cfg.prompt_id_tmp
        mock_cfg.valid_domains = lc_cfg.valid_domains
        mock_cfg.memory_db = lc_cfg.memory_db
        sg_mod._graph = None
        with patch.object(sg_mod, "_CHECKPOINTS_DB", cp_path), \
             patch.object(sg_mod, "_SESSIONS_DB", sessions_db_path), \
             patch.object(tn, "_cfg", mock_cfg), \
             patch("sys.stdin", StringIO(json.dumps({"tool_name": "mcp__local-mac__contacts__search", "session_id": "sess-1", "duration_ms": 50, "tool_input": {}, "tool_response": {"name": "Simran", "phoneNumbers": [{"value": "+911234567890"}]}}))), \
             patch("sys.stdout", new_callable=StringIO):
            tul_mod.main()
        sg_mod._graph = None

        # Simulate confirm__send PostToolUse — tool result carries the token
        sg_mod._graph = None
        with patch.object(sg_mod, "_CHECKPOINTS_DB", cp_path), \
             patch.object(sg_mod, "_SESSIONS_DB", sessions_db_path):
            graph = sg_mod.get_session_graph()
            cfg   = sg_mod._config("sess-1")
            existing = graph.get_state(cfg)
            pid = (existing.values or {}).get("prompt_id", "test-pid")
        sg_mod._graph = None
        with patch.object(sg_mod, "_CHECKPOINTS_DB", cp_path), \
             patch.object(sg_mod, "_SESSIONS_DB", sessions_db_path), \
             patch("sys.stdin", StringIO(json.dumps({"tool_name": "mcp__local-mac__confirm__send", "session_id": "sess-1", "duration_ms": 5, "tool_input": {}, "tool_response": {"confirmed": True, "token": pid, "recipient": "+911234567890", "message": "hi"}}))), \
             patch("sys.stdout", new_callable=StringIO):
            tul_mod.main()
        sg_mod._graph = None

        with patch.object(sg_mod, "_CHECKPOINTS_DB", cp_path), \
             patch.object(sg_mod, "_SESSIONS_DB", sessions_db_path):
            result = self._run(
                {"tool_name": "mcp__local-mac__imessage__send", "session_id": "sess-1"},
                sessions_db_path, cp_path,
            )
        assert "hookSpecificOutput" not in result

    def test_gated_tool_allowed_with_confirm_in_previous_turn(self, tmp_path):
        """confirm__send in turn N satisfies gate for imessage__send in turn N+1."""
        sessions_db_path = tmp_path / "sessions.db"
        cp_path = tmp_path / "checkpoints.db"
        import hooks.tool_usage_logger_lc as tul_mod
        import langchain_learning.nodes.log_tool_usage as tn
        from langchain_learning.config import config as lc_cfg
        from src.config import config as src_cfg
        tool_hints_path = tmp_path / "tool_hints.sqlite"
        with sqlite3.connect(str(tool_hints_path)) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS mcp_tool_hints (tool_name TEXT PRIMARY KEY, domain TEXT, count INTEGER DEFAULT 0, last_used TIMESTAMP, avg_latency_ms REAL DEFAULT 0.0, keywords TEXT DEFAULT '', skill TEXT DEFAULT '', recent_prompts TEXT DEFAULT '[]')")
            conn.execute("CREATE TABLE IF NOT EXISTS stopwords (word TEXT PRIMARY KEY)")
            conn.commit()
        mock_cfg = MagicMock()
        mock_cfg.tool_hints_db = tool_hints_path
        mock_cfg.prompt_id_tmp = src_cfg.prompt_id_tmp
        mock_cfg.valid_domains = lc_cfg.valid_domains
        mock_cfg.memory_db = lc_cfg.memory_db

        # Turn 1: contacts__search runs
        sg_mod._graph = None
        with patch.object(sg_mod, "_CHECKPOINTS_DB", cp_path), \
             patch.object(sg_mod, "_SESSIONS_DB", sessions_db_path):
            sg_mod.run_session(prompt="send message to simran", session_id="sess-x", cwd="/tmp")
        sg_mod._graph = None
        with patch.object(sg_mod, "_CHECKPOINTS_DB", cp_path), \
             patch.object(sg_mod, "_SESSIONS_DB", sessions_db_path), \
             patch.object(tn, "_cfg", mock_cfg), \
             patch("sys.stdin", StringIO(json.dumps({"tool_name": "mcp__local-mac__contacts__search", "session_id": "sess-x", "duration_ms": 50, "tool_input": {}, "tool_response": {"name": "Simran", "phoneNumbers": [{"value": "+911234567890"}]}}))), \
             patch("sys.stdout", new_callable=StringIO):
            tul_mod.main()
        sg_mod._graph = None

        # Turn 2 (new UserPromptSubmit — user said "yes"): confirm__send runs
        with patch.object(sg_mod, "_CHECKPOINTS_DB", cp_path), \
             patch.object(sg_mod, "_SESSIONS_DB", sessions_db_path):
            sg_mod.run_session(prompt="yes", session_id="sess-x", cwd="/tmp")
        sg_mod._graph = None
        with patch.object(sg_mod, "_CHECKPOINTS_DB", cp_path), \
             patch.object(sg_mod, "_SESSIONS_DB", sessions_db_path), \
             patch.object(tn, "_cfg", mock_cfg), \
             patch("sys.stdin", StringIO(json.dumps({"tool_name": "mcp__local-mac__confirm__send", "session_id": "sess-x", "duration_ms": 5, "tool_input": {}, "tool_response": {"confirmed": True, "recipient": "+911234567890", "message": "hi"}}))), \
             patch("sys.stdout", new_callable=StringIO):
            tul_mod.main()
        sg_mod._graph = None

        # Turn 3 (new UserPromptSubmit): imessage__send — gate should see confirm__send in previous turn
        with patch.object(sg_mod, "_CHECKPOINTS_DB", cp_path), \
             patch.object(sg_mod, "_SESSIONS_DB", sessions_db_path):
            sg_mod.run_session(prompt="retry send", session_id="sess-x", cwd="/tmp")
        sg_mod._graph = None
        with patch.object(sg_mod, "_CHECKPOINTS_DB", cp_path), \
             patch.object(sg_mod, "_SESSIONS_DB", sessions_db_path):
            result = self._run(
                {"tool_name": "mcp__local-mac__imessage__send", "session_id": "sess-x"},
                sessions_db_path, cp_path,
            )
        assert "hookSpecificOutput" not in result, f"Expected allow but got deny: {result}"

    def test_mail_compose_denied_without_prereq(self, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        SessionDB.open(sessions_db_path)
        result = self._run(
            {"tool_name": "mcp__local-mac__mail__compose", "session_id": "s2", "tool_use_id": "p2"},
            sessions_db_path
        )
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def test_ungated_mcp_tool_allowed(self, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        SessionDB.open(sessions_db_path)
        result = self._run(
            {"tool_name": "mcp__local-mac__music__play", "session_id": "s3", "tool_use_id": "p3"},
            sessions_db_path
        )
        assert "hookSpecificOutput" not in result

    def test_fail_open_on_missing_session_id(self, tmp_path):
        result = self._run(
            {"tool_name": "mcp__local-mac__imessage__send"},
            tmp_path / "sessions.db"
        )
        assert result == {}

    def test_prereq_from_different_prompt_still_denied(self, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        cp_path = tmp_path / "checkpoints.db"

        # Turn 1: UserPromptSubmit + contacts__search PostToolUse (prereq recorded)
        sg_mod._graph = None
        with patch.object(sg_mod, "_CHECKPOINTS_DB", cp_path), \
             patch.object(sg_mod, "_SESSIONS_DB", sessions_db_path):
            sg_mod.run_session(prompt="find alice", session_id="sess-x", cwd="/tmp")
            sg_mod.run_post_tool("mcp__local-mac__contacts__search", {}, session_id="sess-x", duration_ms=30)
        sg_mod._graph = None

        # Turn 2: new UserPromptSubmit — resets prompt_tools to []
        sg_mod._graph = None
        with patch.object(sg_mod, "_CHECKPOINTS_DB", cp_path), \
             patch.object(sg_mod, "_SESSIONS_DB", sessions_db_path):
            sg_mod.run_session(prompt="now send message", session_id="sess-x", cwd="/tmp")
        sg_mod._graph = None

        # Gate on new prompt — contacts__search not in this prompt's prompt_tools → deny
        result = self._run(
            {"tool_name": "mcp__local-mac__imessage__send", "session_id": "sess-x"},
            sessions_db_path, cp_path,
        )
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


# ---------------------------------------------------------------------------
# tool_usage_logger_lc
# ---------------------------------------------------------------------------

class TestToolUsageLoggerLc:
    def _run(self, hook_input: dict, tool_hints_path: Path, sessions_db_path: Path,
             checkpoints_db_path: Path | None = None) -> dict:
        import hooks.tool_usage_logger_lc as hook_mod
        import langchain_learning.nodes.log_tool_usage as tn
        sg_mod._graph = None
        cp_path = checkpoints_db_path or (sessions_db_path.parent / "checkpoints.db")

        from langchain_learning.config import config as lc_cfg
        from src.config import config as src_cfg
        mock_cfg = MagicMock()
        mock_cfg.tool_hints_db = tool_hints_path
        mock_cfg.prompt_id_tmp = src_cfg.prompt_id_tmp
        mock_cfg.valid_domains = lc_cfg.valid_domains
        mock_cfg.memory_db = lc_cfg.memory_db

        with patch.object(sg_mod, "_CHECKPOINTS_DB", cp_path), \
             patch.object(sg_mod, "_SESSIONS_DB", sessions_db_path), \
             patch.object(tn, "_cfg", mock_cfg), \
             patch("sys.stdin", StringIO(json.dumps(hook_input))), \
             patch("sys.stdout", new_callable=StringIO) as mock_out:
            hook_mod.main()
            out = mock_out.getvalue().strip()

        sg_mod._graph = None
        return json.loads(out) if out else {}

    def test_upserts_new_tool_hint(self, tool_hints_db, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        self._run(
            {"tool_name": "mcp__local-mac__aq__current_dasha", "session_id": "s1",
             "duration_ms": 120, "tool_input": {}, "prompt_id": "p1"},
            tool_hints_db, sessions_db_path
        )
        with sqlite3.connect(str(tool_hints_db)) as conn:
            row = conn.execute(
                "SELECT count, domain FROM mcp_tool_hints WHERE tool_name = 'aq__current_dasha'"
            ).fetchone()
        assert row is not None
        assert row[0] == 1
        assert row[1] == "astrology"

    def test_increments_existing_hint(self, tool_hints_db, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        for _ in range(3):
            self._run(
                {"tool_name": "mcp__local-mac__aq__current_dasha", "session_id": "s1",
                 "duration_ms": 100, "tool_input": {}, "prompt_id": "p1"},
                tool_hints_db, sessions_db_path
            )
        with sqlite3.connect(str(tool_hints_db)) as conn:
            row = conn.execute(
                "SELECT count FROM mcp_tool_hints WHERE tool_name = 'aq__current_dasha'"
            ).fetchone()
        assert row[0] == 3

    def test_non_mcp_tool_skipped(self, tool_hints_db, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        self._run(
            {"tool_name": "Bash", "session_id": "s1", "duration_ms": 50},
            tool_hints_db, sessions_db_path
        )
        with sqlite3.connect(str(tool_hints_db)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM mcp_tool_hints").fetchone()[0]
        assert count == 0

    def test_memory_tool_skipped(self, tool_hints_db, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        self._run(
            {"tool_name": "mcp__local-mac__memory__search", "session_id": "s1", "duration_ms": 20},
            tool_hints_db, sessions_db_path
        )
        with sqlite3.connect(str(tool_hints_db)) as conn:
            count = conn.execute("SELECT COUNT(*) FROM mcp_tool_hints").fetchone()[0]
        assert count == 0

    def test_records_prompt_tool_in_state(self, tool_hints_db, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        cp_path = tmp_path / "checkpoints.db"

        # Seed checkpoint via run_session
        sg_mod._graph = None
        with patch.object(sg_mod, "_CHECKPOINTS_DB", cp_path), \
             patch.object(sg_mod, "_SESSIONS_DB", sessions_db_path):
            sg_mod.run_session(prompt="search contact", session_id="sess-1", cwd="/tmp")
        sg_mod._graph = None

        self._run(
            {"tool_name": "mcp__local-mac__contacts__search", "session_id": "sess-1",
             "duration_ms": 80, "tool_input": {"query": "kuna"}},
            tool_hints_db, sessions_db_path, cp_path,
        )

        # Verify prompt_tools in checkpoint state contains contacts__search
        with patch.object(sg_mod, "_CHECKPOINTS_DB", cp_path):
            cp_state = sg_mod.get_session_graph().get_state({"configurable": {"thread_id": "sess-1"}})
        sg_mod._graph = None
        prompt_tools = cp_state.values.get("prompt_tools") or []
        assert any(
            (isinstance(t, dict) and t.get("tool") == "contacts__search")
            or t == "contacts__search"
            for t in prompt_tools
        )

    def test_avg_latency_computed_correctly(self, tool_hints_db, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        for ms in [100, 200]:
            self._run(
                {"tool_name": "mcp__local-mac__contacts__search", "session_id": "s1",
                 "duration_ms": ms, "tool_input": {}, "prompt_id": "p"},
                tool_hints_db, sessions_db_path
            )
        with sqlite3.connect(str(tool_hints_db)) as conn:
            row = conn.execute(
                "SELECT avg_latency_ms FROM mcp_tool_hints WHERE tool_name = 'contacts__search'"
            ).fetchone()
        assert row[0] == 150.0


# ---------------------------------------------------------------------------
# stop_hook_lc
# ---------------------------------------------------------------------------

class TestStopHookLc:
    def _run(self, hook_input: dict, sessions_db_path: Path) -> dict:
        import hooks.stop_hook_lc as hook_mod
        sg_mod._graph = None

        with patch.object(sg_mod, "_SESSIONS_DB", sessions_db_path), \
             patch("sys.stdin", StringIO(json.dumps(hook_input))), \
             patch("sys.stdout", new_callable=StringIO) as mock_out:
            hook_mod.main()
            out = mock_out.getvalue().strip()

        sg_mod._graph = None
        return json.loads(out) if out else {}

    def test_stop_hook_is_noop(self, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        SessionDB.open(sessions_db_path)
        result = self._run({"session_id": "any-session"}, sessions_db_path)
        assert result == {}
