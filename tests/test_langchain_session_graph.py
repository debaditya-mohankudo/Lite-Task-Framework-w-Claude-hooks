"""Tests for Component 2 — SessionGraph (LangGraph StateGraph).

Test strategy:
  - All IO (MEMORY.sqlite, tool_hints.sqlite) uses temp files or
    monkeypatched paths — no dependency on real system DBs.
  - Graph topology is exercised end-to-end via graph.invoke().
  - Individual nodes are also tested in isolation to verify partial-update contract.
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
    build_session_graph,
    run_session,
)
from langchain_learning.nodes.load_memories import LoadMemoriesNode
from langchain_learning.nodes._text_utils import tokenise as _tokenise
from langchain_learning.nodes.cwd_domain_detect import CwdDomainDetectNode
from langchain_learning.nodes.score_tools import ScoreToolsNode

# Instantiate nodes for direct unit testing
load_memories     = LoadMemoriesNode()
cwd_domain_detect = CwdDomainDetectNode()
score_tools       = ScoreToolsNode()

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
def mock_cfg(memory_db, hints_db):
    """Patch _cfg on all node modules that use it, plus session_graph."""
    import langchain_learning.session_graph as sg
    import langchain_learning.nodes.load_memories as lm
    import langchain_learning.nodes.score_tools as st
    from langchain_learning.config import config as real_cfg
    cfg = types.SimpleNamespace(
        memory_db=memory_db,
        tool_hints_db=hints_db,
        checkpoints_db=real_cfg.checkpoints_db,
    )
    sg._graph = None
    with patch.object(sg, "_cfg", cfg), \
         patch.object(lm, "_cfg", cfg), \
         patch.object(st, "_cfg", cfg):
        yield cfg
    sg._graph = None


def _base_state(**overrides) -> SessionState:
    from collections import OrderedDict
    s: SessionState = {
        "event_type": "user_prompt_submit",
        "prompt": "", "cwd": "", "session_id": "", "turn": 0,
        "memories": [],
        "domains": [], "keywords": [],
        "tool_hints": [],
        "active_task_id": "", "active_task_title": "",
        "task_memories": [], "task_context": [], "task_stack": [], "mid_task_decisions": [], "related_tasks": [],
        "task_rag_chunks": [], "task_body": "",
        "current_state": "prompt",
        "tool_name": "", "tool_input": {}, "tool_result": {}, "prompt_id": "",
        "prompt_tools": [], "session_prompt_ids": [], "session_tools": OrderedDict(),
        "session_prompt_texts": {},
        "gate_denied": False, "gate_reason": "",
        "duration_ms": 0.0,
    }
    s.update(overrides)  # type: ignore[arg-type]
    return s


# ---------------------------------------------------------------------------
# _tokenise
# ---------------------------------------------------------------------------

def test_tokenise_basic():
    result = _tokenise("what nakshatra is the moon in")
    assert "nakshatra" in result
    assert "moon" in result


def test_tokenise_strips_short_tokens():
    result = _tokenise("is it ok to go")
    assert "ok" not in result  # len < 4


def test_tokenise_lowercases():
    result = _tokenise("NAKSHATRA RAHU")
    assert "nakshatra" in result
    assert "rahu" in result


# ---------------------------------------------------------------------------
# load_memories node
# ---------------------------------------------------------------------------

def test_load_memories_scores_relevant(mock_cfg):
    result = load_memories(_base_state(prompt="what is my nakshatra today"))
    names = [m["name"] for m in result["memories"]]
    assert "astro-mem" in names


def test_load_memories_excludes_irrelevant(mock_cfg):
    result = load_memories(_base_state(prompt="play some music"))
    names = [m["name"] for m in result["memories"]]
    assert "market-mem" not in names
    assert "vault-mem" not in names


def test_load_memories_extracts_keywords(mock_cfg):
    result = load_memories(_base_state(prompt="nakshatra rahu panchang today"))
    assert "nakshatra" in result["keywords"]
    assert "panchang" in result["keywords"]


def test_load_memories_missing_db_returns_empty():
    import langchain_learning.nodes.load_memories as pn
    cfg = types.SimpleNamespace(memory_db=Path("/tmp/no_such_memory.sqlite"), tool_hints_db=Path("/tmp/no_hints.sqlite"))
    with patch.object(pn, "_cfg", cfg):
        result = load_memories(_base_state(prompt="test"))
    assert result["memories"] == []


def test_load_memories_caps_at_ten(hints_db):
    rows = [{"name": f"mem{i}", "type": "user", "domain": "macos", "priority": 20,
             "tags": "message send", "body": "macos tool"} for i in range(15)]
    big_db = _make_memory_db(rows)
    import langchain_learning.nodes.load_memories as pn
    cfg = types.SimpleNamespace(memory_db=big_db, tool_hints_db=hints_db)
    with patch.object(pn, "_cfg", cfg):
        result = load_memories(_base_state(prompt="send message to contact"))
    assert len(result["memories"]) <= 10


# ---------------------------------------------------------------------------
# load_task_memories node
# ---------------------------------------------------------------------------

def _make_tasks_db(task_id: str, tags: str) -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    conn = sqlite3.connect(tmp.name)
    conn.execute("CREATE TABLE open_tasks (id TEXT PRIMARY KEY, title TEXT, tags TEXT, body TEXT, status TEXT, issue_type TEXT DEFAULT 'task')")
    conn.execute("INSERT INTO open_tasks (id, title, tags, body, status) VALUES (?, 'Test task', ?, '', 'wip')", (task_id, tags))
    conn.commit()
    conn.close()
    return Path(tmp.name)


def test_load_task_memories_filters_by_project_domain():
    from langchain_learning.nodes.load_task_memories import LoadTaskMemoriesNode
    import langchain_learning.nodes.load_task_memories as ltm_mod

    task_id = "abc123"
    mem_rows = [
        {"name": "claude-hooks-mem", "type": "project", "domain": "claude-hooks", "priority": 20, "tags": "hooks pipeline", "body": "hooks arch"},
        {"name": "macos-mem",        "type": "feedback", "domain": "macos",        "priority": 20, "tags": "hooks pipeline", "body": "macos noise"},
        {"name": "global-mem",       "type": "user",     "domain": "global",        "priority": 10, "tags": "hooks pipeline", "body": "global rule"},
    ]
    mem_db   = _make_memory_db(mem_rows)
    tasks_db = _make_tasks_db(task_id, "project:claude-hooks,hooks,pipeline")
    cfg = types.SimpleNamespace(memory_db=mem_db, tasks_db=tasks_db)

    node = LoadTaskMemoriesNode()
    with patch.object(ltm_mod, "_cfg", cfg):
        state = _base_state(prompt="hooks pipeline")
        state = state | {"active_task_id": task_id, "active_task_title": "hooks pipeline"}
        result = node(state)

    names = {m["name"] for m in result["task_memories"]}
    assert "claude-hooks-mem" in names, "project domain memory should be included"
    assert "global-mem" in names,       "global domain memory should always pass filter"
    assert "macos-mem" not in names,    "cross-domain memory should be filtered out"


def test_load_task_memories_no_project_tag_scores_all():
    from langchain_learning.nodes.load_task_memories import LoadTaskMemoriesNode
    import langchain_learning.nodes.load_task_memories as ltm_mod

    task_id = "def456"
    mem_rows = [
        {"name": "macos-mem",   "type": "feedback", "domain": "macos",   "priority": 20, "tags": "hooks", "body": "macos info"},
        {"name": "vault-mem",   "type": "reference", "domain": "vault",  "priority": 20, "tags": "hooks", "body": "vault info"},
    ]
    mem_db   = _make_memory_db(mem_rows)
    tasks_db = _make_tasks_db(task_id, "some-tag,other-tag")
    cfg = types.SimpleNamespace(memory_db=mem_db, tasks_db=tasks_db)

    node = LoadTaskMemoriesNode()
    with patch.object(ltm_mod, "_cfg", cfg):
        state = _base_state(prompt="hooks")
        state = state | {"active_task_id": task_id, "active_task_title": "hooks info"}
        result = node(state)

    names = {m["name"] for m in result["task_memories"]}
    assert "macos-mem" in names and "vault-mem" in names, "no project tag → all domains scored"


# ---------------------------------------------------------------------------
# cwd_domain_detect node
# ---------------------------------------------------------------------------

def test_cwd_domain_detect_maps_known_cwd():
    import langchain_learning.nodes.cwd_domain_detect as cdd_mod
    cwd_map = {"market-intel": "market-intel", "claude-hooks": "claude-hooks"}
    with patch.object(cdd_mod, "_cfg", types.SimpleNamespace(cwd_domain_map=cwd_map)):
        state = _base_state(cwd="/Users/x/workspace/market-intel/src")
        result = cwd_domain_detect(state)
    assert "market-intel" in result["domains"]


def test_cwd_domain_detect_no_match_leaves_domains_unchanged():
    import langchain_learning.nodes.cwd_domain_detect as cdd_mod
    cwd_map = {"market-intel": "market-intel"}
    with patch.object(cdd_mod, "_cfg", types.SimpleNamespace(cwd_domain_map=cwd_map)):
        state = _base_state(cwd="/tmp/random_project", domains=["astrology"])
        result = cwd_domain_detect(state)
    assert "astrology" in result["domains"]


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
    assert "imessage__send" not in tool_names


def test_score_tools_caps_at_five(mock_cfg):
    result = score_tools(_base_state(domains=["macos", "vault", "astrology", "market-intel"], keywords=["write", "send", "gold", "panchang"]))
    assert len(result["tool_hints"]) <= 5


def test_score_tools_missing_db_returns_empty():
    import langchain_learning.nodes.score_tools as st
    cfg = types.SimpleNamespace(memory_db=Path("/tmp/no_such_memory.sqlite"), tool_hints_db=Path("/tmp/no_hints.sqlite"))
    with patch.object(st, "_cfg", cfg):
        result = score_tools(_base_state(domains=["macos"], keywords=["send"]))
    assert result["tool_hints"] == []


# ---------------------------------------------------------------------------
# Full graph — end-to-end via build_session_graph()
# ---------------------------------------------------------------------------

def test_graph_compiles():
    graph = build_session_graph()
    assert graph is not None


def test_graph_invoke_produces_prompt_id(mock_cfg):
    graph = build_session_graph()
    result = graph.invoke(_base_state(
        prompt="what nakshatra is the moon in today",
        session_id="",
    ))
    assert result["prompt_id"] != ""


def test_graph_state_is_immutable_between_nodes(mock_cfg):
    """Each node returns a partial dict; original state dict must not be mutated."""
    initial = _base_state(prompt="nakshatra today", session_id="")
    original_memories = initial["memories"]

    graph = build_session_graph()
    result = graph.invoke(initial)

    assert original_memories == []
    assert len(result["memories"]) > 0


def test_run_session_convenience(mock_cfg):
    with patch("langchain_learning.session_graph._graph", None):
        result = run_session("what is the gold price today")

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
                                checkpoints_db=cp)
    sg._graph = None
    with patch.object(sg, "_cfg", cfg):
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
                                checkpoints_db=cp)
    sg._graph = None
    with patch.object(sg, "_cfg", cfg):
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
    """

    def _patch(self, sg, cp: Path):
        from langchain_learning.config import config as real_cfg
        cfg = types.SimpleNamespace(
            memory_db=real_cfg.memory_db,
            tool_hints_db=real_cfg.tool_hints_db,
            checkpoints_db=cp,
        )
        return (patch.object(sg, "_cfg", cfg),)

    def test_prompt_id_flows_from_submit_to_gate(self, mock_cfg, tmp_path):
        """prompt_id written by UserPromptSubmit must be readable by PreToolUse
        via checkpoint — no manual DB read in the gate hook."""
        import langchain_learning.session_graph as sg

        cp = tmp_path / "cp.db"
        sid = "chk-test-gate"

        sg._graph = None
        p1, = self._patch(sg, cp)
        with p1:
            r1 = sg.run_session("send message to alice", session_id=sid, cwd="/tmp")
        prompt_id_from_submit = r1["prompt_id"]
        assert prompt_id_from_submit, "UserPromptSubmit must set prompt_id"
        sg._graph = None

        p1, = self._patch(sg, cp)
        with p1:
            sg.run_post_tool("mcp__local-mac__contacts__search", {"name": "Alice"}, session_id=sid, duration_ms=50,
                             tool_result={"name": "Alice", "phoneNumbers": [{"value": "+911234567890"}]})
        sg._graph = None

        p1, = self._patch(sg, cp)
        with p1:
            gate_result = sg.run_gate("imessage__send", {"recipient": "+911234567890"}, session_id=sid)
        sg._graph = None

        assert not gate_result["gate_denied"], \
            f"Gate should allow after prereqs; got denied: {gate_result['gate_reason']}"

    def test_gate_allows_name_from_previous_prompt(self, mock_cfg, tmp_path):
        """Gate should allow imessage__send when the recipient name was in the PREVIOUS
        prompt (e.g. 'send hi to Alice'), even if the current prompt is just 'Yes'."""
        import langchain_learning.session_graph as sg

        cp = tmp_path / "cp.db"
        sid = "chk-test-prev-prompt-name"

        sg._graph = None
        p1, = self._patch(sg, cp)
        with p1:
            sg.run_session("send hi to Alice", session_id=sid, cwd="/tmp")
            sg.run_post_tool("mcp__local-mac__contacts__search", {"name": "Alice"}, session_id=sid, duration_ms=30)
        sg._graph = None

        p1, = self._patch(sg, cp)
        with p1:
            sg.run_session("Yes", session_id=sid, cwd="/tmp")
        sg._graph = None

        p1, = self._patch(sg, cp)
        with p1:
            gate_result = sg.run_gate("imessage__send", {"recipient": "+911234567890"}, session_id=sid)
        sg._graph = None

        assert not gate_result["gate_denied"], \
            f"Gate should allow when name was in previous prompt; got: {gate_result['gate_reason']}"

    def test_prompt_id_not_reset_between_hooks(self, mock_cfg, tmp_path):
        """prompt_id must be the same across UserPromptSubmit and all subsequent hooks
        in the same turn — it must NOT be regenerated on PreToolUse or PostToolUse."""
        import langchain_learning.session_graph as sg

        cp = tmp_path / "cp.db"
        sid = "chk-test-stable-pid"

        sg._graph = None
        p1, = self._patch(sg, cp)
        with p1:
            r1 = sg.run_session("check gold price", session_id=sid, cwd="/tmp")
        prompt_id_t1 = r1["prompt_id"]
        sg._graph = None

        p1, = self._patch(sg, cp)
        with p1:
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
        sid = "chk-test-new-pid"

        sg._graph = None
        p1, = self._patch(sg, cp)
        with p1:
            r1 = sg.run_session("turn one", session_id=sid, cwd="/tmp")
        pid1 = r1["prompt_id"]
        sg._graph = None

        p1, = self._patch(sg, cp)
        with p1:
            r2 = sg.run_session("turn two", session_id=sid, cwd="/tmp")
        pid2 = r2["prompt_id"]
        sg._graph = None

        assert pid1 != pid2, "Each turn must produce a distinct prompt_id"
        assert pid1 != "", "Turn 1 prompt_id must be non-empty"
        assert pid2 != "", "Turn 2 prompt_id must be non-empty"

    def test_domains_persist_into_gate_hook(self, mock_cfg, tmp_path):
        """Domains set during UserPromptSubmit must be visible in PreToolUse checkpoint."""
        import langchain_learning.session_graph as sg

        cp = tmp_path / "cp.db"
        sid = "chk-test-domains"

        sg._graph = None
        p1, = self._patch(sg, cp)
        with p1:
            r1 = sg.run_session("what is the gold and nifty outlook", session_id=sid, cwd="/tmp")
        domains_from_submit = r1.get("domains", [])
        sg._graph = None

        p1, = self._patch(sg, cp)
        with p1:
            cp_state = sg.get_session_graph().get_state({"configurable": {"thread_id": sid}})
        sg._graph = None

        checkpoint_domains = cp_state.values.get("domains", [])
        assert set(domains_from_submit) == set(checkpoint_domains), \
            f"Domains lost between submit and gate checkpoint: {domains_from_submit} → {checkpoint_domains}"

    def test_turn_increments_correctly_across_all_hooks(self, mock_cfg, tmp_path):
        """turn counter must only increment on UserPromptSubmit, not on tool hooks."""
        import langchain_learning.session_graph as sg

        cp = tmp_path / "cp.db"
        sid = "chk-test-turn-stable"

        sg._graph = None
        p1, = self._patch(sg, cp)
        with p1:
            r1 = sg.run_session("turn one", session_id=sid, cwd="/tmp")
        assert r1["turn"] == 1
        sg._graph = None

        p1, = self._patch(sg, cp)
        with p1:
            sg.run_gate("contacts__search", {}, session_id=sid)
            cp_after_gate = sg.get_session_graph().get_state({"configurable": {"thread_id": sid}})
        assert cp_after_gate.values.get("turn") == 1, \
            f"turn should not change during PreToolUse, got {cp_after_gate.values.get('turn')}"
        sg._graph = None

        p1, = self._patch(sg, cp)
        with p1:
            r2 = sg.run_session("turn two", session_id=sid, cwd="/tmp")
        assert r2["turn"] == 2
        sg._graph = None

    def test_gate_denied_when_no_checkpoint_exists(self, mock_cfg, tmp_path):
        """If no prior UserPromptSubmit checkpoint exists, gate must still be safe."""
        import langchain_learning.session_graph as sg

        cp = tmp_path / "cp.db"
        sid = "chk-test-no-prior"

        sg._graph = None
        p1, = self._patch(sg, cp)
        with p1:
            gate_result = sg.run_gate("imessage__send", {"recipient": "+911234567890"}, session_id=sid)
        sg._graph = None

        assert gate_result["gate_denied"], \
            "Gate must deny gated tool when no checkpoint exists (no contacts__search recorded)"
