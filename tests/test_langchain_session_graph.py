"""Tests for Component 2 — SessionGraph (LangGraph StateGraph).

Test strategy:
  - All IO (MEMORY.sqlite, tool_hints.sqlite, sessions.db) uses temp files or
    monkeypatched paths — no dependency on real system DBs.
  - Graph topology is exercised end-to-end via graph.invoke().
  - Individual nodes are also tested in isolation to verify partial-update contract.
  - Conditional edge routing (skip_tools vs score_tools path) has dedicated tests.
"""
import json
import sqlite3
import tempfile
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from langchain_learning.session_state import SessionState
from langchain_learning.session_graph import (
    _route_after_classify,
    build_session_graph,
    run_session,
)
from langchain_learning.nodes.prompt_nodes import (
    LoadMemoriesNode,
    ClassifyDomainNode,
    ScoreToolsNode,
    PersistSessionNode,
    _tokenise,
)

# Instantiate nodes for direct unit testing (mirrors ACME registry pattern)
_load_memories   = LoadMemoriesNode()
_classify_domain = ClassifyDomainNode()
_score_tools     = ScoreToolsNode()
_persist_session = PersistSessionNode()

# Aliases matching old function names used in tests
load_memories   = _load_memories
classify_domain = _classify_domain
score_tools     = _score_tools
persist_session = _persist_session


# ---------------------------------------------------------------------------
# Fixtures — temp DBs
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
        "INSERT INTO memories (name, type, domain, priority, tags, body) VALUES (:name,:type,:domain,:priority,:tags,:body)",
        rows,
    )
    conn.commit()
    conn.close()
    return Path(tmp.name)


def _make_hints_db(rows: list[dict]) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    conn = sqlite3.connect(tmp.name)
    conn.execute("""
        CREATE TABLE mcp_tool_hints (
            tool_name TEXT PRIMARY KEY,
            domain TEXT, skill TEXT,
            count INTEGER DEFAULT 0,
            keywords TEXT
        )
    """)
    conn.executemany(
        "INSERT INTO mcp_tool_hints VALUES (:tool_name,:domain,:skill,:count,:keywords)",
        rows,
    )
    conn.commit()
    conn.close()
    return Path(tmp.name)


def _make_sessions_db() -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    conn = sqlite3.connect(tmp.name)
    conn.execute("""
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            keywords TEXT, domains TEXT, injected_names TEXT,
            current_state TEXT, state_history TEXT,
            turn INTEGER DEFAULT 0, updated_at TEXT, tasks TEXT
        )
    """)
    conn.commit()
    conn.close()
    return Path(tmp.name)


@pytest.fixture
def memory_db():
    return _make_memory_db([
        {"name": "always-on", "type": "user", "domain": "global", "priority": 1,
         "tags": "global", "body": "always injected"},
        {"name": "astro-mem", "type": "project", "domain": "astrology", "priority": 20,
         "tags": "nakshatra rahu panchang", "body": "astrology data"},
        {"name": "market-mem", "type": "project", "domain": "market-intel", "priority": 20,
         "tags": "gold nifty fii", "body": "market data"},
        {"name": "vault-mem", "type": "reference", "domain": "vault", "priority": 20,
         "tags": "vault note write", "body": "vault operations"},
    ])


@pytest.fixture
def hints_db():
    return _make_hints_db([
        {"tool_name": "panchang__today",     "domain": "astrology",   "skill": "panchang", "count": 20, "keywords": "panchang,nakshatra,tithi"},
        {"tool_name": "market__gold_regime", "domain": "market-intel","skill": "gold",     "count": 15, "keywords": "gold,regime,market"},
        {"tool_name": "imessage__send",      "domain": "macos",       "skill": "imessage", "count": 50, "keywords": "send,message,contact"},
        {"tool_name": "vault__write",        "domain": "vault",       "skill": "vault",    "count": 40, "keywords": "write,save,note,vault"},
    ])


@pytest.fixture
def sessions_db():
    return _make_sessions_db()


@pytest.fixture
def mock_cfg(memory_db, hints_db):
    """Patch _cfg on all node modules and session_graph with temp DBs."""
    import langchain_learning.session_graph as sg
    import langchain_learning.nodes.prompt_nodes as pn
    from langchain_learning.config import config as real_cfg
    cfg = types.SimpleNamespace(
        memory_db=memory_db,
        tool_hints_db=hints_db,
        valid_domains=real_cfg.valid_domains,
    )
    sg._graph = None  # reset singleton so it picks up patched nodes
    with patch.object(sg, "_cfg", cfg), \
         patch.object(pn, "_cfg", cfg):
        yield cfg
    sg._graph = None


def _base_state(**overrides) -> SessionState:
    s: SessionState = {
        "event_type": "user_prompt_submit",
        "prompt": "", "session_id": "", "turn": 0,
        "memories": [], "session_context": "", "session_context_ids": [],
        "domains": [], "keywords": [],
        "tool_hints": [], "skip_tools": False,
        "tool_name": "", "tool_input": {}, "prompt_id": "",
        "gate_denied": False, "gate_reason": "",
        "duration_ms": 0.0, "tool_use_id": "",
    }
    s.update(overrides)
    return s


# ---------------------------------------------------------------------------
# _tokenise
# ---------------------------------------------------------------------------

def test_tokenise_basic():
    tokens = _tokenise("Send a message to John about panchang")
    assert "send" in tokens
    assert "message" in tokens
    assert "panchang" in tokens


def test_tokenise_strips_short_tokens():
    tokens = _tokenise("go do it")
    assert tokens == []  # all < 3 chars after strip


def test_tokenise_lowercases():
    tokens = _tokenise("NAKSHATRA Rahu")
    assert "nakshatra" in tokens
    assert "rahu" in tokens


# ---------------------------------------------------------------------------
# load_memories node
# ---------------------------------------------------------------------------

def test_load_memories_returns_always_inject(mock_cfg):
    result = load_memories(_base_state(prompt="hello"))
    names = [m["name"] for m in result["memories"]]
    assert "always-on" in names


def test_load_memories_scores_relevant(mock_cfg):
    result = load_memories(_base_state(prompt="what is my nakshatra today"))
    names = [m["name"] for m in result["memories"]]
    assert "astro-mem" in names


def test_load_memories_excludes_irrelevant(mock_cfg):
    result = load_memories(_base_state(prompt="play some music"))
    names = [m["name"] for m in result["memories"]]
    # market and vault not relevant to "play music"
    assert "market-mem" not in names
    assert "vault-mem" not in names


def test_load_memories_extracts_keywords(mock_cfg):
    result = load_memories(_base_state(prompt="nakshatra rahu panchang today"))
    assert "nakshatra" in result["keywords"]
    assert "panchang" in result["keywords"]


def test_load_memories_missing_db_returns_empty():
    import langchain_learning.nodes.prompt_nodes as pn
    from langchain_learning.config import config as real_cfg
    cfg = types.SimpleNamespace(memory_db=Path("/tmp/no_such_memory.sqlite"), tool_hints_db=Path("/tmp/no_hints.sqlite"), valid_domains=real_cfg.valid_domains)
    with patch.object(pn, "_cfg", cfg):
        result = load_memories(_base_state(prompt="test"))
    assert result["memories"] == []


def test_load_memories_caps_at_ten(hints_db):
    rows = [{"name": f"mem{i}", "type": "user", "domain": "macos", "priority": 20,
             "tags": "message send", "body": "macos tool"} for i in range(15)]
    big_db = _make_memory_db(rows)
    import langchain_learning.nodes.prompt_nodes as pn
    from langchain_learning.config import config as real_cfg
    cfg = types.SimpleNamespace(memory_db=big_db, tool_hints_db=hints_db, valid_domains=real_cfg.valid_domains)
    with patch.object(pn, "_cfg", cfg):
        result = load_memories(_base_state(prompt="send message to contact"))
    assert len(result["memories"]) <= 10


# ---------------------------------------------------------------------------
# classify_domain node
# ---------------------------------------------------------------------------

def test_classify_domain_detects_astrology():
    state = _base_state(keywords=["nakshatra", "rahu", "today"], memories=[])
    result = classify_domain(state)
    assert "astrology" in result["domains"]
    assert result["skip_tools"] is False


def test_classify_domain_detects_market():
    state = _base_state(keywords=["gold", "nifty"], memories=[])
    result = classify_domain(state)
    assert "market-intel" in result["domains"]


def test_classify_domain_multi_domain():
    state = _base_state(keywords=["gold", "nakshatra"], memories=[])
    result = classify_domain(state)
    assert "market-intel" in result["domains"]
    assert "astrology" in result["domains"]


def test_classify_domain_uses_memory_signal():
    memories = [{"domain": "vault", "name": "x", "priority": 20, "tags": "", "body": ""}]
    state = _base_state(keywords=["find"], memories=memories)
    result = classify_domain(state)
    assert "vault" in result["domains"]


def test_classify_domain_no_signal_sets_skip():
    state = _base_state(keywords=["hello", "world", "tea"], memories=[])
    result = classify_domain(state)
    assert result["skip_tools"] is True
    assert result["domains"] == []


# ---------------------------------------------------------------------------
# _route_after_classify
# ---------------------------------------------------------------------------

def test_route_skips_tools_when_flag_set():
    state = _base_state(skip_tools=True)
    assert _route_after_classify(state) == "skip_tools"


def test_route_goes_to_score_tools_when_domain_found():
    state = _base_state(skip_tools=False, domains=["macos"])
    assert _route_after_classify(state) == "score_tools"


# ---------------------------------------------------------------------------
# score_tools node
# ---------------------------------------------------------------------------

def test_score_tools_returns_matching_domain(mock_cfg):
    result = score_tools(_base_state(domains=["astrology"], keywords=["nakshatra"]))
    tool_names = [h["tool_name"] for h in result["tool_hints"]]
    assert "panchang__today" in tool_names


def test_score_tools_excludes_non_domain(mock_cfg):
    result = score_tools(_base_state(domains=["astrology"], keywords=["panchang"]))
    tool_names = [h["tool_name"] for h in result["tool_hints"]]
    # imessage has no astrology domain — pure keyword miss too
    assert "imessage__send" not in tool_names


def test_score_tools_caps_at_five(mock_cfg):
    result = score_tools(_base_state(domains=["macos", "vault", "astrology", "market-intel"], keywords=["write", "send", "gold", "panchang"]))
    assert len(result["tool_hints"]) <= 5


def test_score_tools_missing_db_returns_empty():
    import langchain_learning.nodes.prompt_nodes as pn
    from langchain_learning.config import config as real_cfg
    cfg = types.SimpleNamespace(memory_db=Path("/tmp/no_such_memory.sqlite"), tool_hints_db=Path("/tmp/no_hints.sqlite"), valid_domains=real_cfg.valid_domains)
    with patch.object(pn, "_cfg", cfg):
        result = score_tools(_base_state(domains=["macos"], keywords=["send"]))
    assert result["tool_hints"] == []


# ---------------------------------------------------------------------------
# persist_session node
# ---------------------------------------------------------------------------

def test_persist_session_upserts_new(sessions_db):
    import langchain_learning.session_graph as sg
    original = sg._SESSIONS_DB
    try:
        sg._SESSIONS_DB = sessions_db
        state = _base_state(session_id="test-session-123", turn=0, domains=["macos"], keywords=["send"])
        result = persist_session(state)
    finally:
        sg._SESSIONS_DB = original

    assert result["turn"] == 1

    conn = sqlite3.connect(str(sessions_db))
    row = conn.execute("SELECT keywords, domains, turn FROM sessions WHERE session_id='test-session-123'").fetchone()
    conn.close()
    assert row is not None
    assert row[2] == 1  # turn incremented


def test_persist_session_no_session_id_still_increments():
    result = persist_session(_base_state(session_id="", turn=5))
    assert result["turn"] == 6


# ---------------------------------------------------------------------------
# Full graph — end-to-end via build_session_graph()
# ---------------------------------------------------------------------------

def test_graph_compiles():
    graph = build_session_graph()
    assert graph is not None


def test_graph_invoke_astrology_prompt(mock_cfg):
    graph = build_session_graph()
    result = graph.invoke(_base_state(
        prompt="what nakshatra is the moon in today",
        session_id="",
    ))

    assert "astrology" in result["domains"]
    assert result["skip_tools"] is False
    assert any(h["tool_name"] == "panchang__today" for h in result["tool_hints"])
    assert result["turn"] == 1


def test_graph_invoke_generic_prompt_skips_tools(mock_cfg):
    graph = build_session_graph()
    result = graph.invoke(_base_state(
        prompt="hello there what time is it",
        session_id="",
    ))

    assert result["domains"] == []
    assert result["skip_tools"] is True
    assert result["tool_hints"] == []


def test_graph_state_is_immutable_between_nodes(mock_cfg):
    """Each node returns a partial dict; original state dict must not be mutated."""
    initial = _base_state(prompt="nakshatra today", session_id="")
    original_memories = initial["memories"]

    graph = build_session_graph()
    result = graph.invoke(initial)

    # original state dict's memories list is unchanged (LangGraph replaces, not mutates)
    assert original_memories == []
    assert len(result["memories"]) > 0


def test_run_session_convenience(mock_cfg):
    with patch("langchain_learning.session_graph._graph", None):  # reset singleton
        result = run_session("what is the gold price today")

    assert "market-intel" in result["domains"]
    assert result["turn"] == 1
