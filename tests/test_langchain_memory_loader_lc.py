"""Tests for hooks/memory_loader_lc.py — LangChain Option A hook.

Strategy:
  - Test prompt extraction, system prompt formatting, and pipeline wiring.
  - No real MEMORY.sqlite or tool_hints.sqlite — all IO uses temp DBs or mocks.
  - No stdin/stdout — test internal functions directly.
  - Pipeline invocation tested end-to-end with temp DBs.
"""
import json
import sqlite3
import sys
import tempfile
from io import StringIO
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure hooks/ is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "hooks"))

from memory_loader_lc import (
    _extract_prompt,
    _format_system_prompt,
    _get_pipeline,
    main,
)
from langchain_learning.pipeline import MemoryContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_memory_db(rows: list[dict]) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    conn = sqlite3.connect(tmp.name)
    conn.execute("""
        CREATE TABLE memories (
            id INTEGER PRIMARY KEY,
            name TEXT, type TEXT, domain TEXT,
            priority INTEGER DEFAULT 50,
            tags TEXT, body TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO memories (name,type,domain,priority,tags,body) VALUES (:name,:type,:domain,:priority,:tags,:body)",
        rows,
    )
    conn.commit()
    conn.close()
    return Path(tmp.name)


def _make_tool_hints_db(rows: list[dict]) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    conn = sqlite3.connect(tmp.name)
    conn.execute("""
        CREATE TABLE mcp_tool_hints (
            tool_name TEXT PRIMARY KEY,
            domain TEXT, skill TEXT,
            count INTEGER DEFAULT 0,
            last_used TEXT DEFAULT '',
            avg_latency_ms REAL DEFAULT 0.0,
            keywords TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO mcp_tool_hints (tool_name,domain,skill,count,keywords) VALUES (:tool_name,:domain,:skill,:count,:keywords)",
        rows,
    )
    conn.commit()
    conn.close()
    return Path(tmp.name)


@pytest.fixture
def memory_db():
    return _make_memory_db([
        {"name": "always-on", "type": "user", "domain": "global", "priority": 1,
         "tags": "global", "body": "always injected context"},
        {"name": "astro-mem", "type": "project", "domain": "astrology", "priority": 20,
         "tags": "nakshatra rahu panchang", "body": "astrology memory body"},
    ])


@pytest.fixture
def tool_hints_db():
    return _make_tool_hints_db([
        {"tool_name": "panchang__today", "domain": "astrology", "skill": "panchang",
         "count": 20, "keywords": "panchang,nakshatra,tithi"},
    ])


# ---------------------------------------------------------------------------
# _extract_prompt
# ---------------------------------------------------------------------------

def test_extract_prompt_direct():
    assert _extract_prompt({"prompt": "hello world"}) == "hello world"


def test_extract_prompt_from_message_string():
    hook = {"message": {"content": "tell me nakshatra"}}
    assert _extract_prompt(hook) == "tell me nakshatra"


def test_extract_prompt_from_message_blocks():
    hook = {"message": {"content": [
        {"type": "text", "text": "what is "},
        {"type": "text", "text": "my nakshatra"},
    ]}}
    assert _extract_prompt(hook) == "what is my nakshatra"


def test_extract_prompt_prefers_direct_over_message():
    hook = {"prompt": "direct", "message": {"content": "indirect"}}
    assert _extract_prompt(hook) == "direct"


def test_extract_prompt_empty_returns_empty():
    assert _extract_prompt({}) == ""


def test_extract_prompt_skips_non_text_blocks():
    hook = {"message": {"content": [
        {"type": "image", "url": "x"},
        {"type": "text", "text": "valid text"},
    ]}}
    assert _extract_prompt(hook) == "valid text"


# ---------------------------------------------------------------------------
# _format_system_prompt
# ---------------------------------------------------------------------------

def _ctx(**overrides) -> MemoryContext:
    base: MemoryContext = {
        "prompt": "test", "domains": [], "memories": [], "tool_hints": []
    }
    base.update(overrides)
    return base


def test_format_includes_domains():
    ctx = _ctx(domains=["astrology", "macos"])
    result = _format_system_prompt(ctx)
    assert "astrology" in result
    assert "macos" in result
    assert "Active domains" in result


def test_format_includes_memory_name_and_body():
    ctx = _ctx(
        domains=["astrology"],
        memories=[{"name": "astro-mem", "domain": "astrology", "body": "nakshatra data here"}],
    )
    result = _format_system_prompt(ctx)
    assert "astro-mem" in result
    assert "nakshatra data here" in result


def test_format_includes_tool_hints():
    ctx = _ctx(
        domains=["astrology"],
        tool_hints=[{"tool_name": "panchang__today", "skill": "panchang", "count": 20}],
    )
    result = _format_system_prompt(ctx)
    assert "panchang__today" in result
    assert "skill=panchang" in result


def test_format_empty_context_returns_empty_string():
    ctx = _ctx()
    assert _format_system_prompt(ctx) == ""


def test_format_no_tool_hints_section_when_empty():
    ctx = _ctx(domains=["macos"], memories=[], tool_hints=[])
    result = _format_system_prompt(ctx)
    assert "Suggested tools" not in result


def test_format_no_memories_section_when_empty():
    ctx = _ctx(domains=["macos"], memories=[], tool_hints=[])
    result = _format_system_prompt(ctx)
    assert "Injected memories" not in result


# ---------------------------------------------------------------------------
# _get_pipeline — singleton
# ---------------------------------------------------------------------------

def test_get_pipeline_returns_runnable():
    pipeline = _get_pipeline()
    assert pipeline is not None
    assert hasattr(pipeline, "invoke")


def test_get_pipeline_is_singleton():
    p1 = _get_pipeline()
    p2 = _get_pipeline()
    assert p1 is p2


# ---------------------------------------------------------------------------
# main() — end-to-end via stdin mock
# ---------------------------------------------------------------------------

def _run_main(hook_input: dict, cwd: str = "") -> dict:
    stdin_data = json.dumps(hook_input)
    with (
        patch("sys.stdin", StringIO(stdin_data)),
        patch("os.environ.get", side_effect=lambda k, d=None: cwd if k == "CLAUDE_CWD" else d),
        patch("memory_loader_lc._write_vault_keywords"),
        patch("memory_loader_lc._PROMPT_TEXT_TMP") as mock_tmp,
    ):
        mock_tmp.write_text = lambda x: None
        captured = StringIO()
        with patch("sys.stdout", captured):
            main()
    output = captured.getvalue().strip()
    return json.loads(output) if output else {}


def test_main_empty_prompt_writes_empty_json(memory_db, tool_hints_db):
    with patch("memory_loader_lc._pipeline", None):
        result = _run_main({"prompt": ""})
    assert result == {}


def test_main_returns_additional_system_prompt(memory_db, tool_hints_db):
    from langchain_learning import pipeline as pl
    import memory_loader_lc as lc
    lc._pipeline = None  # reset singleton

    lc._pipeline = pl.build_memory_pipeline(
        use_llm=False,
        memory_db=memory_db,
        tool_hints_db=tool_hints_db,
    )

    stdin_data = json.dumps({"prompt": "nakshatra today"})
    with patch("sys.stdin", StringIO(stdin_data)):
        with patch("os.environ.get", side_effect=lambda k, d=None: "" if k == "CLAUDE_CWD" else d):
            with patch("memory_loader_lc._write_vault_keywords"):
                with patch("memory_loader_lc._PROMPT_TEXT_TMP") as m:
                    m.write_text = lambda x: None
                    captured = StringIO()
                    with patch("sys.stdout", captured):
                        main()

    output = json.loads(captured.getvalue().strip())
    assert "hookSpecificOutput" in output
    assert "additionalSystemPrompt" in output["hookSpecificOutput"]
    prompt_text = output["hookSpecificOutput"]["additionalSystemPrompt"]
    # always-on memory should always appear
    assert "always-on" in prompt_text
