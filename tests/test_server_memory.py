"""Tests for server-owned session memory (hooks/server_memory.py) and its accessor."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent
for _p in (str(_PROJECT_ROOT), str(_PROJECT_ROOT / "hooks")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hooks.server_memory as sm


@pytest.fixture(autouse=True)
def _clean_store():
    sm.reset()
    yield
    sm.reset()


# ── record_prompt ─────────────────────────────────────────────────────────────

def test_record_and_get_prompts():
    sm.record_prompt("s1", "hello")
    sm.record_prompt("s1", "world")
    out = sm.get_server_memory()
    assert [p["text"] for p in out["prompts"]] == ["hello", "world"]
    assert out["prompts"][0]["claude_session_id"] == "s1"
    assert out["n_prompts_total"] == 2


def test_empty_prompt_is_noop():
    sm.record_prompt("s1", "")
    assert sm.get_server_memory()["prompts"] == []


def test_prompt_cap_keeps_last_max():
    for i in range(sm._MAX_PROMPTS + 25):
        sm.record_prompt("s1", f"p{i}")
    out = sm.get_server_memory(n_prompts=10_000)
    assert len(out["prompts"]) == sm._MAX_PROMPTS
    assert out["prompts"][-1]["text"] == f"p{sm._MAX_PROMPTS + 24}"  # newest kept


# ── record_task ───────────────────────────────────────────────────────────────

def test_record_and_get_tasks():
    sm.record_task("s1", "t1", "First")
    sm.record_task("s1", "t2", "Second")
    out = sm.get_server_memory()
    assert [(t["task_id"], t["title"]) for t in out["tasks"]] == [("t1", "First"), ("t2", "Second")]


def test_empty_task_id_is_noop():
    sm.record_task("s1", "", "no id")
    assert sm.get_server_memory()["tasks"] == []


def test_task_dedup_consecutive():
    sm.record_task("s1", "t1", "First")
    sm.record_task("s1", "t1", "First again")   # same id back-to-back → skipped
    sm.record_task("s1", "t2", "Second")
    sm.record_task("s1", "t1", "First returns") # not consecutive → recorded
    ids = [t["task_id"] for t in sm.get_server_memory()["tasks"]]
    assert ids == ["t1", "t2", "t1"]


# ── get_server_memory bounds ──────────────────────────────────────────────────

def test_get_bounds_last_n_and_m():
    for i in range(5):
        sm.record_prompt("s", f"p{i}")
    for i in range(5):
        sm.record_task("s", f"t{i}", f"T{i}")
    out = sm.get_server_memory(n_prompts=2, m_tasks=3)
    assert [p["text"] for p in out["prompts"]] == ["p3", "p4"]
    assert [t["task_id"] for t in out["tasks"]] == ["t2", "t3", "t4"]
    # totals reflect everything, not the window
    assert out["n_prompts_total"] == 5 and out["n_tasks_total"] == 5


def test_get_empty_returns_valid_dict():
    out = sm.get_server_memory()
    assert out["prompts"] == [] and out["tasks"] == []
    assert "server_session_id" in out and out["server_session_id"]


def test_zero_window_returns_empty_lists():
    sm.record_prompt("s", "x")
    out = sm.get_server_memory(n_prompts=0, m_tasks=0)
    assert out["prompts"] == [] and out["tasks"] == []
    assert out["n_prompts_total"] == 1


# ── accessor endpoint ─────────────────────────────────────────────────────────

def test_session_memory_endpoint():
    import langchain_learning.session_graph as sg_mod
    from fastapi.testclient import TestClient
    from hooks.server import app

    sm.record_prompt("s1", "endpoint hello")
    sm.record_task("s1", "tX", "Endpoint task")

    sg_mod._graph = None
    with TestClient(app) as c:
        r = c.get("/session/memory", params={"n_prompts": 5, "m_tasks": 5})
    sg_mod._graph = None

    assert r.status_code == 200
    data = r.json()
    assert data["prompts"][-1]["text"] == "endpoint hello"
    assert data["tasks"][-1]["task_id"] == "tX"


# ── MCP wrapper ───────────────────────────────────────────────────────────────

# ── record_task_from_hook (real PostToolUse payload shape) ────────────────────

def test_record_task_from_hook_fully_qualified_and_wrapped():
    """Fully-qualified MCP tool_name + wrapped tool_response → task recorded with title."""
    body = {
        "session_id": "s1",
        "tool_name": "mcp__claude-hooks__tasks__set_active",
        "tool_input": {"task_id": "abc123"},
        "tool_response": {"content": [{"type": "text", "text": '{"ok": true, "task_id": "abc123", "title": "Do the thing"}'}]},
    }
    sm.record_task_from_hook(body)
    tasks = sm.get_server_memory()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["task_id"] == "abc123"
    assert tasks[0]["title"] == "Do the thing"


def test_record_task_from_hook_unwrapped_response():
    """Plain (already-unwrapped) tool_response dict also yields the title."""
    body = {
        "session_id": "s1",
        "tool_name": "mcp__claude-hooks__tasks__set_active",
        "tool_input": {"task_id": "xyz"},
        "tool_response": {"title": "Plain title"},
    }
    sm.record_task_from_hook(body)
    assert sm.get_server_memory()["tasks"][0]["title"] == "Plain title"


def test_title_resolved_from_db_wins_over_response(tmp_path):
    """Title comes authoritatively from proj_tasks.db, not the brittle response envelope."""
    import sqlite3
    db = tmp_path / "proj_tasks.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE open_tasks (id TEXT PRIMARY KEY, title TEXT)")
    conn.execute("INSERT INTO open_tasks VALUES ('t1', 'From DB')")
    conn.commit()
    conn.close()

    cfg = MagicMock()
    cfg.tasks_db = db
    body = {
        "session_id": "s",
        "tool_name": "mcp__claude-hooks__tasks__set_active",
        "tool_input": {"task_id": "t1"},
        "tool_response": {"title": "from response"},
    }
    with patch("langchain_learning.config.config", cfg):
        sm.record_task_from_hook(body)
    assert sm.get_server_memory()["tasks"][0]["title"] == "From DB"


def test_record_task_from_hook_ignores_other_mcp_tools():
    body = {"session_id": "s1", "tool_name": "mcp__claude-hooks__tasks__finish", "tool_input": {"task_id": "abc"}}
    sm.record_task_from_hook(body)
    assert sm.get_server_memory()["tasks"] == []


def test_record_task_from_hook_ignores_non_mcp():
    body = {"session_id": "s1", "tool_name": "Bash", "tool_input": {}}
    sm.record_task_from_hook(body)
    assert sm.get_server_memory()["tasks"] == []


def test_mcp_wrapper_returns_error_when_server_down():
    import src.tools.hooks as h
    with patch("src.tools.hooks.urllib.request.urlopen", side_effect=OSError("refused")):
        out = h.handle_server_memory()
    assert "error" in out


def test_mcp_wrapper_parses_server_response():
    import src.tools.hooks as h
    payload = b'{"prompts": [{"text": "hi"}], "tasks": []}'
    cm = MagicMock()
    cm.__enter__.return_value.read.return_value = payload
    with patch("src.tools.hooks.urllib.request.urlopen", return_value=cm):
        out = h.handle_server_memory(n_prompts=3, m_tasks=2)
    assert out["prompts"][0]["text"] == "hi"
