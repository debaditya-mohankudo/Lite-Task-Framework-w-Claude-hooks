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
from core.db.session_db import SessionDB
from langchain_learning.nodes.load_memories import LoadMemoriesNode
from langchain_learning.nodes._text_utils import tokenise as _tokenise
from langchain_learning.nodes.cwd_domain_detect import CwdDomainDetectNode
from langchain_learning.nodes.keyword_score import KeywordScoreNode, _iter_domains, _score_domain
from langchain_learning.nodes.combination_score import CombinationScoreNode
from langchain_learning.nodes.memory_domain_signal import MemoryDomainSignalNode
from langchain_learning.nodes.apply_threshold import ApplyThresholdNode
from langchain_learning.nodes.score_tools import ScoreToolsNode

# Instantiate nodes for direct unit testing (mirrors ACME registry pattern)
load_memories          = LoadMemoriesNode()
cwd_domain_detect      = CwdDomainDetectNode()
keyword_score          = KeywordScoreNode()
combination_score      = CombinationScoreNode()
memory_domain_signal   = MemoryDomainSignalNode()
apply_threshold        = ApplyThresholdNode()
score_tools            = ScoreToolsNode()

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
    """Patch _cfg on all node modules that use it, plus session_graph."""
    import langchain_learning.session_graph as sg
    import langchain_learning.nodes.load_memories as lm
    import langchain_learning.nodes.classify_domain as cd
    import langchain_learning.nodes.score_tools as st
    from langchain_learning.config import config as real_cfg
    cfg = types.SimpleNamespace(
        memory_db=memory_db,
        tool_hints_db=hints_db,
        valid_domains=real_cfg.valid_domains,
        checkpoints_db=real_cfg.checkpoints_db,
    )
    sg._graph = None
    with patch.object(sg, "_cfg", cfg), \
         patch.object(lm, "_cfg", cfg), \
         patch.object(cd, "_cfg", cfg), \
         patch.object(st, "_cfg", cfg):
        yield cfg
    sg._graph = None


def _base_state(**overrides) -> SessionState:
    s: SessionState = {
        "event_type": "user_prompt_submit",
        "prompt": "", "cwd": "", "session_id": "",
        "memories": [], "prompt_context": {},
        "domains": [], "keywords": [],
        "tool_hints": [], "skip_tools": False,
        "classifier_scores": {}, "matched_keywords": [],
        "tool_name": "", "tool_input": {}, "tool_result": {}, "prompt_id": "",
        "gate_denied": False, "gate_reason": "",
        "duration_ms": 0.0,
    }
    s.update(overrides)
    return s


# ---------------------------------------------------------------------------
# Shared real classifier config (loaded once for classify chain tests)
# ---------------------------------------------------------------------------

def _real_classifier_config() -> dict:
    """Load the real domain_classifier.json for classify chain tests."""
    from src.config import config as src_cfg
    import json
    with open(src_cfg.domain_classifier_json) as f:
        return json.load(f)


def _with_real_cfg(fn):
    """Decorator: patch get_classifier_config cache so nodes see the real config."""
    import functools
    import langchain_learning.nodes.load_classifier_config as lcn
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        original = lcn._cache
        lcn._cache = _real_classifier_config()
        try:
            return fn(*args, **kwargs)
        finally:
            lcn._cache = original
    return wrapper


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
    assert tokens == set()  # all < 3 chars after strip


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
    import langchain_learning.nodes.load_memories as pn
    from langchain_learning.config import config as real_cfg
    cfg = types.SimpleNamespace(memory_db=Path("/tmp/no_such_memory.sqlite"), tool_hints_db=Path("/tmp/no_hints.sqlite"), valid_domains=real_cfg.valid_domains)
    with patch.object(pn, "_cfg", cfg):
        result = load_memories(_base_state(prompt="test"))
    assert result["memories"] == []


def test_load_memories_caps_at_ten(hints_db):
    rows = [{"name": f"mem{i}", "type": "user", "domain": "macos", "priority": 20,
             "tags": "message send", "body": "macos tool"} for i in range(15)]
    big_db = _make_memory_db(rows)
    import langchain_learning.nodes.load_memories as pn
    from langchain_learning.config import config as real_cfg
    cfg = types.SimpleNamespace(memory_db=big_db, tool_hints_db=hints_db, valid_domains=real_cfg.valid_domains)
    with patch.object(pn, "_cfg", cfg):
        result = load_memories(_base_state(prompt="send message to contact"))
    assert len(result["memories"]) <= 10


# ---------------------------------------------------------------------------
# cwd_domain_detect node
# ---------------------------------------------------------------------------

@_with_real_cfg
def test_cwd_domain_detect_maps_known_cwd():
    state = _base_state(cwd="/Users/x/workspace/market-intel/src")
    result = cwd_domain_detect(state)
    assert "market-intel" in result["domains"]


@_with_real_cfg
def test_cwd_domain_detect_no_match_leaves_domains_unchanged():
    state = _base_state(cwd="/tmp/random_project", domains=["astrology"])
    result = cwd_domain_detect(state)
    assert "astrology" in result["domains"]


# ---------------------------------------------------------------------------
# keyword_score node
# ---------------------------------------------------------------------------

@_with_real_cfg
def test_keyword_score_scores_astrology():
    state = _base_state(prompt="what nakshatra is rahu transiting today")
    result = keyword_score(state)
    assert result["classifier_scores"].get("astrology", 0) > 0
    assert "nakshatra" in result["matched_keywords"] or "rahu" in result["matched_keywords"]


@_with_real_cfg
def test_keyword_score_scores_market():
    state = _base_state(prompt="what is the gold and nifty outlook")
    result = keyword_score(state)
    assert result["classifier_scores"].get("market-intel", 0) > 0


@_with_real_cfg
def test_keyword_score_negative_signal_suppresses_domain():
    state = _base_state(prompt="visit the supermarket today")
    result = keyword_score(state)
    assert result["classifier_scores"].get("market-intel", 0) == 0


@_with_real_cfg
def test_keyword_score_empty_prompt_returns_empty():
    state = _base_state(prompt="")
    result = keyword_score(state)
    assert result["classifier_scores"] == {}
    assert result["matched_keywords"] == []


# ---------------------------------------------------------------------------
# _iter_domains / _score_domain unit tests
# ---------------------------------------------------------------------------

def test_iter_domains_yields_all_without_negatives():
    signals = {"astrology": {"strong": {"rahu": 10}}, "market": {"strong": {"gold": 5}}}
    results = list(_iter_domains(signals, {}, "rahu transiting"))
    assert [d for d, _ in results] == ["astrology", "market"]


def test_iter_domains_skips_negative_match():
    signals  = {"market": {"strong": {"gold": 5}}}
    negative = {"market": ["supermarket"]}
    results  = list(_iter_domains(signals, negative, "visit the supermarket"))
    assert results == []


def test_iter_domains_yields_domain_when_negative_absent():
    signals  = {"market": {"strong": {"gold": 5}}}
    negative = {"market": ["supermarket"]}
    results  = list(_iter_domains(signals, negative, "what is the gold price"))
    assert len(results) == 1
    assert results[0][0] == "market"


def test_score_domain_strong_signal():
    groups = {"strong": {"rahu": 10, "nakshatra": 8}, "weak": {}}
    tokens = {"rahu", "transiting", "today"}
    score, matched = _score_domain(groups, "rahu transiting today", tokens)
    assert score == 10
    assert "rahu" in matched


def test_score_domain_phrase_signal():
    groups = {"strong": {"gold price": 15}, "weak": {}}
    tokens = {"what", "is", "the", "gold", "price"}
    score, matched = _score_domain(groups, "what is the gold price", tokens)
    assert score == 15
    assert "gold" in matched and "price" in matched


def test_score_domain_no_match_returns_zero():
    groups = {"strong": {"rahu": 10}, "weak": {"nakshatra": 3}}
    tokens = {"hello", "world"}
    score, matched = _score_domain(groups, "hello world", tokens)
    assert score == 0
    assert matched == set()


# ---------------------------------------------------------------------------
# combination_score node
# ---------------------------------------------------------------------------

@_with_real_cfg
def test_combination_score_adds_bonus():
    state = _base_state(
        prompt="send a message to my contact",
        classifier_scores={"macos": 2},
        matched_keywords=["message"],
    )
    result = combination_score(state)
    assert result["classifier_scores"].get("macos", 0) > 2


@_with_real_cfg
def test_combination_score_accumulates_on_existing_scores():
    state = _base_state(
        prompt="nakshatra rahu transiting today",
        classifier_scores={"astrology": 9},
        matched_keywords=["nakshatra", "rahu"],
    )
    result = combination_score(state)
    assert result["classifier_scores"].get("astrology", 0) >= 9


# ---------------------------------------------------------------------------
# memory_domain_signal node
# ---------------------------------------------------------------------------

def test_memory_domain_signal_adds_from_memories():
    import langchain_learning.nodes.memory_domain_signal as mds
    from langchain_learning.config import config as real_cfg
    memories = [{"domain": "vault", "name": "x", "priority": 20, "tags": "", "body": ""}]
    state = _base_state(memories=memories, domains=[])
    with patch.object(mds, "_cfg", types.SimpleNamespace(valid_domains=real_cfg.valid_domains)):
        result = memory_domain_signal(state)
    assert "vault" in result["domains"]


def test_memory_domain_signal_ignores_global_domain():
    import langchain_learning.nodes.memory_domain_signal as mds
    from langchain_learning.config import config as real_cfg
    memories = [{"domain": "global", "name": "x", "priority": 1, "tags": "", "body": ""}]
    state = _base_state(memories=memories, domains=[])
    with patch.object(mds, "_cfg", types.SimpleNamespace(valid_domains=real_cfg.valid_domains)):
        result = memory_domain_signal(state)
    assert result["domains"] == []


def test_memory_domain_signal_caps_at_three():
    import langchain_learning.nodes.memory_domain_signal as mds
    from langchain_learning.config import config as real_cfg
    memories = [
        {"domain": "astrology",   "name": "a", "priority": 20, "tags": "", "body": ""},
        {"domain": "vault",       "name": "b", "priority": 20, "tags": "", "body": ""},
        {"domain": "market-intel","name": "c", "priority": 20, "tags": "", "body": ""},
        {"domain": "macos",       "name": "d", "priority": 20, "tags": "", "body": ""},
    ]
    state = _base_state(memories=memories, domains=[])
    with patch.object(mds, "_cfg", types.SimpleNamespace(valid_domains=real_cfg.valid_domains)):
        result = memory_domain_signal(state)
    assert len(result["domains"]) == 3


# ---------------------------------------------------------------------------
# apply_threshold node
# ---------------------------------------------------------------------------

@_with_real_cfg
def test_apply_threshold_filters_by_threshold():
    cfg = _real_classifier_config()
    threshold = cfg.get("classify_threshold", 2)
    state = _base_state(
        classifier_scores={"astrology": threshold + 3, "market-intel": threshold - 1},
        matched_keywords=["nakshatra"],
        domains=[],
    )
    result = apply_threshold(state)
    assert "astrology" in result["domains"]
    assert "market-intel" not in result["domains"]
    assert result["skip_tools"] is False


@_with_real_cfg
def test_apply_threshold_defaults_to_macos_when_nothing_scores():
    state = _base_state(classifier_scores={}, matched_keywords=[], domains=[])
    result = apply_threshold(state)
    assert result["skip_tools"] is False
    assert "macos" in result["domains"]


@_with_real_cfg
def test_apply_threshold_merges_existing_domains():
    state = _base_state(
        classifier_scores={"astrology": 9},
        matched_keywords=[],
        domains=["macos"],
    )
    result = apply_threshold(state)
    assert "macos" in result["domains"]
    assert "astrology" in result["domains"]


@_with_real_cfg
def test_apply_threshold_enriches_keywords():
    state = _base_state(
        classifier_scores={"astrology": 9},
        matched_keywords=["nakshatra", "rahu"],
        keywords=["today"],
        domains=[],
    )
    result = apply_threshold(state)
    assert "nakshatra" in result["keywords"]
    assert "rahu" in result["keywords"]
    assert "today" not in result["keywords"]  # stopword filtered


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
    import langchain_learning.nodes.score_tools as st
    from langchain_learning.config import config as real_cfg
    cfg = types.SimpleNamespace(memory_db=Path("/tmp/no_such_memory.sqlite"), tool_hints_db=Path("/tmp/no_hints.sqlite"), valid_domains=real_cfg.valid_domains)
    with patch.object(st, "_cfg", cfg):
        result = score_tools(_base_state(domains=["macos"], keywords=["send"]))
    assert result["tool_hints"] == []


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
    assert result["prompt_id"] != ""  # set_prompt_id generated a UUID


def test_graph_invoke_generic_prompt_defaults_to_macos(mock_cfg):
    graph = build_session_graph()
    result = graph.invoke(_base_state(
        prompt="hello there what time is it",
        session_id="",
    ))

    assert "macos" in result["domains"]
    assert result["skip_tools"] is False


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
    assert result["prompt_id"] != ""


# ---------------------------------------------------------------------------
# MemorySaver — turn counter persists across invocations on the same thread
# ---------------------------------------------------------------------------

def test_turn_increments_across_invocations(mock_cfg, tmp_path):
    """Turn must increment each UserPromptSubmit on the same session thread via SqliteSaver."""
    import langchain_learning.session_graph as sg
    from langchain_learning.config import config as real_cfg

    cp = tmp_path / "cp.db"
    cfg = types.SimpleNamespace(memory_db=mock_cfg.memory_db, tool_hints_db=mock_cfg.tool_hints_db,
                                valid_domains=mock_cfg.valid_domains, checkpoints_db=cp)
    sg._graph = None
    with patch.object(sg, "_cfg", cfg), \
         patch.object(sg, "_SESSIONS_DB", tmp_path / "sessions.db"):
        r1 = sg.run_session("hello", session_id="turn-test", cwd="/tmp")
        assert r1["turn"] == 1

        sg._graph = None
        r2 = sg.run_session("hello again", session_id="turn-test", cwd="/tmp")
        assert r2["turn"] == 2

        sg._graph = None
        r3 = sg.run_session("one more", session_id="turn-test", cwd="/tmp")
        assert r3["turn"] == 3
    sg._graph = None


def test_thread_isolation(mock_cfg, tmp_path):
    """Different session_ids must not share turn state via SqliteSaver."""
    import langchain_learning.session_graph as sg

    cp = tmp_path / "cp.db"
    cfg = types.SimpleNamespace(memory_db=mock_cfg.memory_db, tool_hints_db=mock_cfg.tool_hints_db,
                                valid_domains=mock_cfg.valid_domains, checkpoints_db=cp)
    sg._graph = None
    with patch.object(sg, "_cfg", cfg), \
         patch.object(sg, "_SESSIONS_DB", tmp_path / "sessions.db"):
        sg.run_session("prompt 1", session_id="sess-a", cwd="/tmp")
        sg._graph = None
        ra = sg.run_session("prompt 2", session_id="sess-a", cwd="/tmp")
        sg._graph = None
        rb = sg.run_session("prompt 1", session_id="sess-b", cwd="/tmp")
    sg._graph = None

    assert ra["turn"] == 2
    assert rb["turn"] == 1


# ---------------------------------------------------------------------------
# Cross-hook checkpoint integration tests
# ---------------------------------------------------------------------------

class TestCheckpointCrossHook:
    """Verify that prompt_id and other state flow correctly across all four
    hook invocations (UserPromptSubmit → PreToolUse → PostToolUse → Stop)
    via the SqliteSaver checkpoint — no DB reads mid-session.

    This test class exists to prevent regression to the old pattern where
    each hook was invoked with a blank state, causing prompt_id to be lost
    between hook processes.
    """

    def _make_sessions_db(self, path: Path) -> "SessionDB":
        db = SessionDB.open(path)
        return db

    def _patch(self, sg, cp: Path, sessions_db: Path):
        from langchain_learning.config import config as real_cfg
        import langchain_learning.nodes.load_memories as lm
        import langchain_learning.nodes.score_tools as st
        cfg = types.SimpleNamespace(
            memory_db=real_cfg.memory_db,
            tool_hints_db=real_cfg.tool_hints_db,
            valid_domains=real_cfg.valid_domains,
            checkpoints_db=cp,
        )
        return patch.object(sg, "_cfg", cfg), \
               patch.object(sg, "_SESSIONS_DB", sessions_db)

    def test_prompt_id_flows_from_submit_to_gate(self, mock_cfg, tmp_path):
        """prompt_id written by UserPromptSubmit must be readable by PreToolUse
        via checkpoint — no manual DB read in the gate hook."""
        import langchain_learning.session_graph as sg

        cp = tmp_path / "cp.db"
        sessions_db_path = tmp_path / "sessions.db"
        self._make_sessions_db(sessions_db_path)
        sid = "chk-test-gate"

        # Step 1: UserPromptSubmit
        sg._graph = None
        p1, p2 = self._patch(sg, cp, sessions_db_path)
        with p1, p2:
            r1 = sg.run_session("send message to alice", session_id=sid, cwd="/tmp")
        prompt_id_from_submit = r1["prompt_id"]
        assert prompt_id_from_submit, "UserPromptSubmit must set prompt_id"
        sg._graph = None

        # Step 2: PostToolUse — simulate contacts__search completing (appends to prompt_tools in state)
        p1, p2 = self._patch(sg, cp, sessions_db_path)
        with p1, p2:
            sg.run_post_tool("mcp__local-mac__contacts__search", {"name": "Simran"}, session_id=sid, duration_ms=50,
                             tool_result={"name": "Simran", "phoneNumbers": [{"value": "+911234567890"}]})
        sg._graph = None

        # Step 3: PreToolUse — gate should now allow imessage__send
        p1, p2 = self._patch(sg, cp, sessions_db_path)
        with p1, p2:
            gate_result = sg.run_gate("imessage__send", {"recipient": "+911234567890"}, session_id=sid)
        sg._graph = None

        # Gate should ALLOW because contacts__search was recorded
        assert not gate_result["gate_denied"], \
            f"Gate should allow after prereqs; got denied: {gate_result['gate_reason']}"

    def test_prompt_id_not_reset_between_hooks(self, mock_cfg, tmp_path):
        """prompt_id must be the same across UserPromptSubmit and all subsequent hooks
        in the same turn — it must NOT be regenerated on PreToolUse or PostToolUse."""
        import langchain_learning.session_graph as sg

        cp = tmp_path / "cp.db"
        sessions_db_path = tmp_path / "sessions.db"
        self._make_sessions_db(sessions_db_path)
        sid = "chk-test-stable-pid"

        # UserPromptSubmit — captures prompt_id
        sg._graph = None
        p1, p2 = self._patch(sg, cp, sessions_db_path)
        with p1, p2:
            r1 = sg.run_session("check gold price", session_id=sid, cwd="/tmp")
        prompt_id_t1 = r1["prompt_id"]
        sg._graph = None

        # PreToolUse — checkpoint must supply the same prompt_id
        p1, p2 = self._patch(sg, cp, sessions_db_path)
        with p1, p2:
            cp_after_gate = sg.get_session_graph().get_state({"configurable": {"thread_id": sid}})
            sg.run_gate("contacts__search", {}, session_id=sid)
        prompt_id_in_gate_checkpoint = cp_after_gate.values.get("prompt_id", "")
        sg._graph = None

        assert prompt_id_in_gate_checkpoint == prompt_id_t1, \
            f"prompt_id changed between submit and gate: {prompt_id_t1!r} → {prompt_id_in_gate_checkpoint!r}"

    def test_new_turn_gets_new_prompt_id(self, mock_cfg, tmp_path):
        """Each UserPromptSubmit must generate a fresh prompt_id, replacing the prior one."""
        import langchain_learning.session_graph as sg

        cp = tmp_path / "cp.db"
        sessions_db_path = tmp_path / "sessions.db"
        sid = "chk-test-new-pid"

        sg._graph = None
        p1, p2 = self._patch(sg, cp, sessions_db_path)
        with p1, p2:
            r1 = sg.run_session("turn one", session_id=sid, cwd="/tmp")
        pid1 = r1["prompt_id"]
        sg._graph = None

        p1, p2 = self._patch(sg, cp, sessions_db_path)
        with p1, p2:
            r2 = sg.run_session("turn two", session_id=sid, cwd="/tmp")
        pid2 = r2["prompt_id"]
        sg._graph = None

        assert pid1 != pid2, "Each turn must produce a distinct prompt_id"
        assert pid1 != "", "Turn 1 prompt_id must be non-empty"
        assert pid2 != "", "Turn 2 prompt_id must be non-empty"

    def test_domains_persist_into_gate_hook(self, mock_cfg, tmp_path):
        """Domains classified during UserPromptSubmit must be visible in PreToolUse checkpoint."""
        import langchain_learning.session_graph as sg

        cp = tmp_path / "cp.db"
        sessions_db_path = tmp_path / "sessions.db"
        sid = "chk-test-domains"

        sg._graph = None
        p1, p2 = self._patch(sg, cp, sessions_db_path)
        with p1, p2:
            r1 = sg.run_session("what is the gold and nifty outlook", session_id=sid, cwd="/tmp")
        domains_from_submit = r1.get("domains", [])
        sg._graph = None

        # PreToolUse — checkpoint should still have domains from submit
        p1, p2 = self._patch(sg, cp, sessions_db_path)
        with p1, p2:
            cp_state = sg.get_session_graph().get_state({"configurable": {"thread_id": sid}})
        sg._graph = None

        checkpoint_domains = cp_state.values.get("domains", [])
        assert set(domains_from_submit) == set(checkpoint_domains), \
            f"Domains lost between submit and gate checkpoint: {domains_from_submit} → {checkpoint_domains}"

    def test_turn_increments_correctly_across_all_hooks(self, mock_cfg, tmp_path):
        """turn counter must only increment on UserPromptSubmit, not on tool hooks."""
        import langchain_learning.session_graph as sg

        cp = tmp_path / "cp.db"
        sessions_db_path = tmp_path / "sessions.db"
        sid = "chk-test-turn-stable"

        sg._graph = None
        p1, p2 = self._patch(sg, cp, sessions_db_path)
        with p1, p2:
            r1 = sg.run_session("turn one", session_id=sid, cwd="/tmp")
        assert r1["turn"] == 1
        sg._graph = None

        # PreToolUse — turn must still be 1
        p1, p2 = self._patch(sg, cp, sessions_db_path)
        with p1, p2:
            sg.run_gate("contacts__search", {}, session_id=sid)
            cp_after_gate = sg.get_session_graph().get_state({"configurable": {"thread_id": sid}})
        assert cp_after_gate.values.get("turn") == 1, \
            f"turn should not change during PreToolUse, got {cp_after_gate.values.get('turn')}"
        sg._graph = None

        # Next UserPromptSubmit — turn must become 2
        p1, p2 = self._patch(sg, cp, sessions_db_path)
        with p1, p2:
            r2 = sg.run_session("turn two", session_id=sid, cwd="/tmp")
        assert r2["turn"] == 2
        sg._graph = None

    def test_gate_denied_when_no_checkpoint_exists(self, mock_cfg, tmp_path):
        """If no prior UserPromptSubmit checkpoint exists, gate must still be safe
        (prompt_id will be empty, so prompt_had_tool returns False → deny gated tools)."""
        import langchain_learning.session_graph as sg

        cp = tmp_path / "cp.db"  # fresh — no checkpoint written yet
        sessions_db_path = tmp_path / "sessions.db"
        sid = "chk-test-no-prior"

        sg._graph = None
        p1, p2 = self._patch(sg, cp, sessions_db_path)
        with p1, p2:
            gate_result = sg.run_gate("imessage__send", {"recipient": "+911234567890"}, session_id=sid)
        sg._graph = None

        assert gate_result["gate_denied"], \
            "Gate must deny gated tool when no checkpoint exists (no contacts__search recorded)"
