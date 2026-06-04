"""Tests for hook → session_graph integration.

All hook modules now delegate to session_graph nodes. Tests patch
session_graph._SESSIONS_DB and session_graph._cfg to redirect DB paths
to tmp directories.

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
    """Gate enforcement — tests patch session_graph._SESSIONS_DB."""

    def _run(self, hook_input: dict, sessions_db_path: Path) -> dict:
        import hooks.pre_tool_use_lc as hook_mod
        # reset graph singleton so it picks up patched _SESSIONS_DB
        sg_mod._graph = None

        with patch.object(sg_mod, "_SESSIONS_DB", sessions_db_path), \
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
        db = SessionDB.open(sessions_db_path)
        # Seed session row with prompt_id + contacts__search call under that prompt
        db.upsert("sess-1", {"keywords": set(), "domains": set(), "injected_names": set(),
                              "current_state": "prompt", "state_history": [], "tasks": [], "turn": 1})
        db.set_prompt_id("sess-1", "prompt-1")
        db.record_prompt_tool("prompt-1", "sess-1", "contacts__search")

        result = self._run(
            {"tool_name": "mcp__local-mac__imessage__send", "session_id": "sess-1", "tool_use_id": "prompt-1"},
            sessions_db_path
        )
        assert "hookSpecificOutput" not in result

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
        db = SessionDB.open(sessions_db_path)
        db.record_prompt_tool("old-prompt", "sess-x", "contacts__search")

        result = self._run(
            {"tool_name": "mcp__local-mac__imessage__send", "session_id": "sess-x", "tool_use_id": "new-prompt"},
            sessions_db_path
        )
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


# ---------------------------------------------------------------------------
# tool_usage_logger_lc
# ---------------------------------------------------------------------------

class TestToolUsageLoggerLc:
    def _run(self, hook_input: dict, tool_hints_path: Path, sessions_db_path: Path) -> dict:
        import hooks.tool_usage_logger_lc as hook_mod
        import langchain_learning.nodes.log_tool_usage as tn
        sg_mod._graph = None

        from langchain_learning.config import config as lc_cfg
        from src.config import config as src_cfg
        mock_cfg = MagicMock()
        mock_cfg.tool_hints_db = tool_hints_path
        mock_cfg.prompt_id_tmp = src_cfg.prompt_id_tmp
        mock_cfg.valid_domains = lc_cfg.valid_domains
        mock_cfg.memory_db = lc_cfg.memory_db

        with patch.object(sg_mod, "_SESSIONS_DB", sessions_db_path), \
             patch.object(tn, "_cfg", mock_cfg), \
             patch.object(tn, "_PROMPT_KW_TMP", Path("/dev/null")), \
             patch.object(tn, "_PROMPT_TEXT_TMP", Path("/dev/null")), \
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

    def test_records_prompt_tool_in_sessions_db(self, tool_hints_db, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        db = SessionDB.open(sessions_db_path)
        # Seed session row with prompt_id — log_tool_usage reads it from DB
        db.upsert("sess-1", {"keywords": set(), "domains": set(), "injected_names": set(),
                              "current_state": "prompt", "state_history": [], "tasks": [], "turn": 1})
        db.set_prompt_id("sess-1", "prompt-abc")
        self._run(
            {"tool_name": "mcp__local-mac__contacts__search", "session_id": "sess-1",
             "duration_ms": 80, "tool_input": {"query": "kuna"}, "prompt_id": "prompt-abc"},
            tool_hints_db, sessions_db_path
        )
        assert db.prompt_had_tool("prompt-abc", "contacts__search") is True

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

    def _seed_session(self, db: SessionDB, session_id: str, keywords: list[str], turn: int = 1):
        db.upsert(session_id, {
            "keywords": set(keywords),
            "domains": {"macos"},
            "injected_names": set(),
            "current_state": "prompt",
            "state_history": [],
            "tasks": [],
            "turn": turn,
        })

    def test_empty_session_id_skips_gracefully(self, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        SessionDB.open(sessions_db_path)
        result = self._run({"session_id": ""}, sessions_db_path)
        assert result == {}

    def test_missing_session_in_db_skips_gracefully(self, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        SessionDB.open(sessions_db_path)
        result = self._run({"session_id": "nonexistent"}, sessions_db_path)
        assert result == {}

    def test_zero_turn_session_skips(self, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        db = SessionDB.open(sessions_db_path)
        self._seed_session(db, "sess-1", ["dasha"], turn=0)
        result = self._run({"session_id": "sess-1"}, sessions_db_path)
        assert result == {}

    def test_persists_session_and_sets_stop_state(self, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        db = SessionDB.open(sessions_db_path)
        self._seed_session(db, "sess-2", ["dasha", "rahu"], turn=3)

        self._run({"session_id": "sess-2"}, sessions_db_path)

        saved = db.get("sess-2")
        assert saved is not None
        assert saved["current_state"] == "stop"

    def test_stopwords_filtered_on_persist(self, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        db = SessionDB.open(sessions_db_path)
        self._seed_session(db, "sess-3", ["the", "and", "for", "dasha", "rahu"], turn=2)

        self._run({"session_id": "sess-3"}, sessions_db_path)

        saved = db.get("sess-3")
        keywords = set(saved["keywords"])
        assert "dasha" in keywords
        assert "rahu" in keywords
        assert "the" not in keywords
        assert "and" not in keywords
        assert "for" not in keywords

    def test_nonexistent_sessions_db_skips_gracefully(self, tmp_path):
        result = self._run({"session_id": "s1"}, tmp_path / "no_such.db")
        assert result == {}
