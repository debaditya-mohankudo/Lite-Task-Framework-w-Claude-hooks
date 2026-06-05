"""Task Graph — separate LangGraph StateGraph for the task_activate event.

Graph shape:

    START → set_active_task → load_task_memories → END

Shares the same SqliteSaver checkpointer DB (langgraph_checkpoints.db) and
SessionState as session_graph, keyed by the same session_id (thread_id).
This means active_task_id and task_memories written here are immediately
visible to the next session_graph invocation for the same session.

Entry point:
    run_task_activate(task_id, session_id) — called by tasks__set_active MCP tool.
"""
from __future__ import annotations

import sqlite3
from collections import OrderedDict
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph, START, END

from langchain_learning.nodes.registry import get_node
from langchain_learning.nodes._node_log import wrap
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_CHECKPOINTS_DB: Path = Path.home() / ".claude" / "langgraph_checkpoints.db"

_graph = None


def build_task_graph(checkpointer=None):
    builder = StateGraph(SessionState)

    for name in ["set_active_task", "load_task_memories"]:
        builder.add_node(name, wrap(name, get_node(name)))

    builder.add_edge(START,               "set_active_task")
    builder.add_edge("set_active_task",   "load_task_memories")
    builder.add_edge("load_task_memories", END)

    return builder.compile(checkpointer=checkpointer)


def get_task_graph():
    global _graph
    if _graph is None:
        conn = sqlite3.connect(str(_CHECKPOINTS_DB), check_same_thread=False)
        _graph = build_task_graph(checkpointer=SqliteSaver(conn))
    return _graph


def _config(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id or "default"}}


def _fresh_state(session_id: str) -> SessionState:
    return SessionState(
        event_type="task_activate", prompt="", cwd="", session_id=session_id,
        turn=0,
        memories=[], prompt_context={},
        domains=[], keywords=[], tool_hints=[], skip_tools=False,
        active_task_id="", active_task_title="", task_memories=[],
        classifier_scores={}, matched_keywords=[],
        current_state="prompt",
        prompt_id="", prompt_tools=[],
        session_prompt_ids=[], session_tools=OrderedDict(),
        gate_denied=False, gate_reason="",
        duration_ms=0.0, tool_result={},
        tool_name="", tool_input={},
    )


def run_task_activate(task_id: str, session_id: str) -> dict:
    """task_activate entry point — called by tasks__set_active MCP tool.

    Writes active_task_id + active_task_title + task_memories into the
    checkpoint for this session_id so the next UPS turn inherits them.

    Returns {active_task_id, active_task_title, task_memories_count}.
    """
    graph = get_task_graph()
    cfg   = _config(session_id)

    existing = graph.get_state(cfg)
    state: SessionState = {
        **(existing.values if existing and existing.values else _fresh_state(session_id)),
        "event_type":     "task_activate",
        "active_task_id": task_id,
        "session_id":     session_id,
    }

    result = graph.invoke(state, config=cfg)
    _log.info(
        "task_activate: session=%s task=%s memories=%d",
        session_id[:8], task_id, len(result.get("task_memories") or []),
    )
    return {
        "active_task_id":    result.get("active_task_id", ""),
        "active_task_title": result.get("active_task_title", ""),
        "task_memories_count": len(result.get("task_memories") or []),
    }


def run_clear_active(session_id: str) -> dict:
    """Clear active task for a session — zeros active_task_id in checkpoint."""
    graph = get_task_graph()
    cfg   = _config(session_id)
    existing = graph.get_state(cfg)
    if not existing or not existing.values:
        return {"cleared": False, "session_id": session_id}
    state: SessionState = {
        **existing.values,
        "event_type":        "task_activate",
        "active_task_id":    "",
        "active_task_title": "",
        "task_memories":     [],
        "session_id":        session_id,
    }
    # Bypass set_active_task (which would error on empty id) — update checkpoint directly
    graph.update_state(cfg, {"active_task_id": "", "active_task_title": "", "task_memories": []})
    return {"cleared": True, "session_id": session_id}
