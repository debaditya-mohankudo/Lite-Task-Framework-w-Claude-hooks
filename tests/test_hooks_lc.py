"""Tests for the three in-process LC hook replacements.

Tests run without any HTTP server — all logic is direct function calls.

Covered:
  - pre_tool_use_lc:      Gate deny/allow, memory-tool skip, fail-open
  - tool_usage_logger_lc: tool_hints upsert, session recording, skip conditions
  - stop_hook_lc:         stopword filtering, state transition, empty-session skip
"""
import json
import sqlite3
import sys
import time
from io import StringIO
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "hooks"))

from core.db.session_db import SessionDB


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
        conn.commit()
    return path


# ---------------------------------------------------------------------------
# pre_tool_use_lc
# ---------------------------------------------------------------------------

class TestPreToolUseLc:
    """Gate enforcement runs directly against sessions.db — no HTTP."""

    def _run(self, hook_input: dict, sessions_db: SessionDB, sessions_db_path: Path) -> tuple[dict, dict]:
        """Run pre_tool_use_lc main() with injected stdin/stdout. Returns (stdout_dict, stderr)."""
        import hooks.pre_tool_use_lc as hook_mod

        output_parts = []

        with patch.object(hook_mod, "_SESSIONS_DB", sessions_db_path), \
             patch("sys.stdin", StringIO(json.dumps(hook_input))), \
             patch("sys.stdout", new_callable=StringIO) as mock_out:
            hook_mod.main()
            out = mock_out.getvalue().strip()

        return json.loads(out) if out else {}

    def test_non_mcp_tool_passes_through(self, sessions_db, tmp_path):
        result = self._run(
            {"tool_name": "Bash", "session_id": "s1", "tool_use_id": "p1"},
            sessions_db, tmp_path / "sessions.db"
        )
        assert result == {}

    def test_memory_tool_skipped(self, sessions_db, tmp_path):
        result = self._run(
            {"tool_name": "mcp__local-mac__memory__add", "session_id": "s1", "tool_use_id": "p1"},
            sessions_db, tmp_path / "sessions.db"
        )
        assert result == {}

    def test_gated_tool_denied_without_prereq(self, sessions_db, tmp_path):
        result = self._run(
            {"tool_name": "mcp__local-mac__imessage__send", "session_id": "s1", "tool_use_id": "p1"},
            sessions_db, tmp_path / "sessions.db"
        )
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def test_gated_tool_allowed_after_prereq(self, sessions_db, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        db = SessionDB.open(sessions_db_path)
        db.record_prompt_tool("prompt-1", "sess-1", "contacts__search")

        from src.config import config as cfg
        cfg.prompt_id_tmp.write_text("prompt-1")

        result = self._run(
            {"tool_name": "mcp__local-mac__imessage__send", "session_id": "sess-1", "tool_use_id": "prompt-1"},
            db, sessions_db_path
        )
        assert "hookSpecificOutput" not in result  # allowed → empty dict

    def test_mail_compose_denied_without_prereq(self, sessions_db, tmp_path):
        result = self._run(
            {"tool_name": "mcp__local-mac__mail__compose", "session_id": "s2", "tool_use_id": "p2"},
            sessions_db, tmp_path / "sessions.db"
        )
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def test_ungated_mcp_tool_allowed(self, sessions_db, tmp_path):
        result = self._run(
            {"tool_name": "mcp__local-mac__music__play", "session_id": "s3", "tool_use_id": "p3"},
            sessions_db, tmp_path / "sessions.db"
        )
        assert "hookSpecificOutput" not in result

    def test_fail_open_on_missing_session_id(self, sessions_db, tmp_path):
        """Missing session_id must not crash — fail-open returns {}."""
        result = self._run(
            {"tool_name": "mcp__local-mac__imessage__send"},
            sessions_db, tmp_path / "sessions.db"
        )
        assert result == {}

    def test_prereq_from_different_prompt_still_denied(self, sessions_db, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        db = SessionDB.open(sessions_db_path)
        db.record_prompt_tool("old-prompt", "sess-x", "contacts__search")

        result = self._run(
            {"tool_name": "mcp__local-mac__imessage__send", "session_id": "sess-x", "tool_use_id": "new-prompt"},
            db, sessions_db_path
        )
        assert result.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


# ---------------------------------------------------------------------------
# tool_usage_logger_lc
# ---------------------------------------------------------------------------

class TestToolUsageLoggerLc:
    def _run(self, hook_input: dict, tool_hints_path: Path, sessions_db_path: Path) -> dict:
        import hooks.tool_usage_logger_lc as hook_mod

        with patch.object(hook_mod, "_TOOL_HINTS_DB", tool_hints_path), \
             patch.object(hook_mod, "_SESSIONS_DB", sessions_db_path), \
             patch.object(hook_mod, "_read_tmp", return_value="dasha,astrology"), \
             patch("sys.stdin", StringIO(json.dumps(hook_input))), \
             patch("sys.stdout", new_callable=StringIO) as mock_out:
            hook_mod.main()
            out = mock_out.getvalue().strip()

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
        from src.config import config as cfg
        sessions_db_path = tmp_path / "sessions.db"
        db = SessionDB.open(sessions_db_path)
        cfg.prompt_id_tmp.write_text("prompt-abc")
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

        with patch.object(hook_mod, "_SESSIONS_DB", sessions_db_path), \
             patch("sys.stdin", StringIO(json.dumps(hook_input))), \
             patch("sys.stdout", new_callable=StringIO) as mock_out:
            hook_mod.main()
            out = mock_out.getvalue().strip()

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
        db = SessionDB.open(sessions_db_path)
        result = self._run({"session_id": ""}, sessions_db_path)
        assert result == {}

    def test_missing_session_in_db_skips_gracefully(self, tmp_path):
        sessions_db_path = tmp_path / "sessions.db"
        db = SessionDB.open(sessions_db_path)
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
        # "the", "and", "for" are stopwords in the project's stopwords.json
        self._seed_session(db, "sess-3", ["the", "and", "for", "dasha", "rahu"], turn=2)

        self._run({"session_id": "sess-3"}, sessions_db_path)

        saved = db.get("sess-3")
        keywords = set(saved["keywords"])
        assert "dasha" in keywords
        assert "rahu" in keywords
        # stopwords must be gone
        assert "the" not in keywords
        assert "and" not in keywords
        assert "for" not in keywords

    def test_nonexistent_sessions_db_skips_gracefully(self, tmp_path):
        result = self._run({"session_id": "s1"}, tmp_path / "no_such.db")
        assert result == {}
