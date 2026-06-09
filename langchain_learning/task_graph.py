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
from typing import cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph, START, END

from langchain_learning.nodes.registry import get_node
from langchain_learning.nodes._node_log import wrap
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

from langchain_learning.config import config as _cfg

_graph = None


def build_task_graph(checkpointer=None):
    """Construct and compile the task activation graph (set_active_task → load_task_memories).

    Tags: task-graph, task-activation, LangGraph, StateGraph, checkpoint
    """
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
        conn = sqlite3.connect(str(_cfg.checkpoints_db), check_same_thread=False)
        _graph = build_task_graph(checkpointer=SqliteSaver(conn))
    return _graph


def _config(session_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": session_id or "default"}}


def _fresh_state(session_id: str) -> SessionState:
    return SessionState(
        event_type="task_activate", prompt="", cwd="", session_id=session_id,
        turn=0,
        memories=[],
        domains=[], keywords=[], tool_hints=[], skip_tools=False,
        active_task_id="", active_task_title="", task_memories=[], task_context=[], task_commits=[], task_stack=[], related_tasks=[],
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

    If a task is already active, pushes it onto task_stack before switching.
    Writes active_task_id + active_task_title + task_memories + task_stack into
    the checkpoint so the next UPS turn inherits them.

    Returns {active_task_id, active_task_title, task_memories_count, task_stack}.
    """
    graph = get_task_graph()
    cfg   = _config(session_id)

    existing       = graph.get_state(cfg)
    existing_vals  = existing.values if existing and existing.values else {}
    current_active = existing_vals.get("active_task_id", "")
    current_stack  = list(existing_vals.get("task_stack") or [])

    if current_active:
        current_stack.append(current_active)
        _log.info("task_stack: pushed %s (depth=%d)", current_active, len(current_stack))

    base = existing_vals if existing_vals else _fresh_state(session_id)
    state: SessionState = cast(SessionState, {
        **base,
        "event_type":     "task_activate",
        "active_task_id": task_id,
        "task_stack":     current_stack,
        "session_id":     session_id,
    })

    result = graph.invoke(state, config=cfg)
    _log.info(
        "task_activate: session=%s task=%s memories=%d stack_depth=%d",
        session_id[:8], task_id, len(result.get("task_memories") or []), len(current_stack),
    )
    return {
        "active_task_id":    result.get("active_task_id", ""),
        "active_task_title": result.get("active_task_title", ""),
        "task_memories_count": len(result.get("task_memories") or []),
        "task_stack":        result.get("task_stack", []),
    }


def run_task_pop(session_id: str) -> dict:
    """Pop the top task from the stack and re-activate it.

    Returns {active_task_id, active_task_title, task_memories_count, task_stack}.
    If stack is empty, clears the active task instead.
    """
    graph = get_task_graph()
    cfg   = _config(session_id)

    existing      = graph.get_state(cfg)
    existing_vals = existing.values if existing and existing.values else {}
    stack         = list(existing_vals.get("task_stack") or [])

    if not stack:
        graph.update_state(cfg, {"active_task_id": "", "active_task_title": "", "task_memories": [], "task_stack": []})
        _log.info("task_pop: stack empty — cleared active task for session %s", session_id[:8])
        return {"active_task_id": "", "active_task_title": "", "task_memories_count": 0, "task_stack": []}

    restored_id = stack.pop()
    base = existing_vals if existing_vals else _fresh_state(session_id)
    state: SessionState = cast(SessionState, {
        **base,
        "event_type":     "task_activate",
        "active_task_id": restored_id,
        "task_stack":     stack,
        "session_id":     session_id,
    })

    result = graph.invoke(state, config=cfg)
    _log.info(
        "task_pop: session=%s restored=%s memories=%d stack_depth=%d",
        session_id[:8], restored_id, len(result.get("task_memories") or []), len(stack),
    )
    return {
        "active_task_id":    result.get("active_task_id", ""),
        "active_task_title": result.get("active_task_title", ""),
        "task_memories_count": len(result.get("task_memories") or []),
        "task_stack":        result.get("task_stack", []),
    }


def run_clear_active(session_id: str) -> dict:
    """Clear active task and stack for a session — zeros checkpoint."""
    graph = get_task_graph()
    cfg   = _config(session_id)
    existing = graph.get_state(cfg)
    if not existing or not existing.values:
        return {"cleared": False, "session_id": session_id}
    graph.update_state(cfg, {"active_task_id": "", "active_task_title": "", "task_memories": [], "task_stack": []})
    return {"cleared": True, "session_id": session_id}
