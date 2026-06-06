"""Session State Machine — unified event graph.

One graph handles all four Claude Code hook events. Each event type routes to
its own chain of nodes, giving full observability across the session lifecycle
in a single graph topology.

Graph shape:

    START → route_event (conditional)
      ├── user_prompt_submit → load_turn → load_memories → load_prompt_context
      │                         → load_classifier_config → cwd_domain_detect
      │                         → load_active_task → keyword_score → combination_score
      │                         → memory_domain_signal → apply_threshold
      │                         → score_tools? → set_prompt_id → END
      ├── pre_tool_use       → gate_check → END
      ├── post_tool_use      → log_tool_usage → update_tool_keywords → END
      └── stop               → log_task_events → END

State persistence: SqliteSaver checkpoints full SessionState to disk after every
invoke, keyed by session_id (thread_id). Each hook process resumes from the prior
checkpoint — no blank-state merging mid-session. Only the first user_prompt_submit
for a new session seeds a fresh state; all subsequent events inject only their
event-specific inputs and let the checkpoint supply everything else (prompt_id,
turn, domains, keywords, etc.).

Node implementations live in langchain_learning/nodes/ — one class per file.
registry.py holds NODE_REGISTRY + get_node() factory.
"""
from __future__ import annotations

from collections import OrderedDict
from pathlib import Path

import sqlite3

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import StateGraph, START, END

from langchain_learning.config import config as _cfg
from langchain_learning.nodes.registry import get_node
from langchain_learning.nodes._node_log import wrap
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

# Injectable for tests — None means auto-detect from home dir
_SESSIONS_DB: Path | None = None

# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

_EVENT_TYPES = {"user_prompt_submit", "pre_tool_use", "post_tool_use", "stop"}


def _route_event(state: SessionState) -> str:
    ev = state.get("event_type", "")
    return ev if ev in _EVENT_TYPES else "unknown"


def _route_after_classify(state: SessionState) -> str:
    return "skip_tools" if state["skip_tools"] else "score_tools"



# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_session_graph(checkpointer=None):
    """Construct and compile the unified session graph.

    Args:
        checkpointer: Optional LangGraph checkpointer (e.g. MemorySaver).
                      When provided, state is retained across invocations by thread_id.
                      When None (default/tests), each invoke is stateless.
    """
    builder = StateGraph(SessionState)

    # Register all nodes from registry
    for name in [
        "noop",
        "load_turn", "load_active_task", "load_task_context", "load_memories", "load_prompt_context",
        "cwd_domain_detect",
        "keyword_score", "combination_score",
        "memory_domain_signal", "apply_threshold",
        "score_tools", "set_prompt_id",
        "gate_check",
        "log_tool_usage", "update_tool_keywords",
        "log_task_events",
    ]:
        builder.add_node(name, wrap(name, get_node(name)))

    # START → conditional route by event_type
    builder.add_conditional_edges(
        START,
        _route_event,
        {
            "user_prompt_submit": "load_turn",
            "pre_tool_use":       "gate_check",
            "post_tool_use":      "log_tool_usage",
            "stop":               "log_task_events",
            "unknown":            "noop",
        },
    )

    # UserPromptSubmit chain
    builder.add_edge("load_turn",             "load_active_task")
    builder.add_edge("load_active_task",      "load_task_context")
    builder.add_edge("load_task_context",     "load_memories")
    builder.add_edge("load_memories",         "load_prompt_context")
    builder.add_edge("load_prompt_context",  "cwd_domain_detect")

    # classify chain
    builder.add_edge("cwd_domain_detect",      "keyword_score")
    builder.add_edge("keyword_score",          "combination_score")
    builder.add_edge("combination_score",      "memory_domain_signal")
    builder.add_edge("memory_domain_signal",   "apply_threshold")

    builder.add_conditional_edges(
        "apply_threshold",
        _route_after_classify,
        {"score_tools": "score_tools", "skip_tools": "set_prompt_id"},
    )
    builder.add_edge("score_tools",   "set_prompt_id")
    builder.add_edge("set_prompt_id", END)

    # PreToolUse chain
    builder.add_edge("gate_check",      END)

    # PostToolUse chain
    builder.add_edge("log_tool_usage",        "update_tool_keywords")
    builder.add_edge("update_tool_keywords",  END)

    # Stop chain
    builder.add_edge("log_task_events", END)

    # Fallback
    builder.add_edge("noop",            END)

    return builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_graph = None
_CHECKPOINTS_DB: Path = Path.home() / ".claude" / "langgraph_checkpoints.db"


def get_session_graph():
    global _graph
    if _graph is None:
        conn = sqlite3.connect(str(_CHECKPOINTS_DB), check_same_thread=False)
        checkpointer = SqliteSaver(conn)
        _graph = build_session_graph(checkpointer=checkpointer)
    return _graph


# ---------------------------------------------------------------------------
# Public entry points (one per hook event)
# ---------------------------------------------------------------------------

def _fresh_state(session_id: str) -> SessionState:
    """Full default state for the very first user_prompt_submit of a new session."""
    return SessionState(
        event_type="", prompt="", cwd="", session_id=session_id,
        turn=0,
        memories=[], prompt_context={},
        domains=[], keywords=[], tool_hints=[], skip_tools=False,
        active_task_id="", active_task_title="", task_memories=[], task_context=[],
        classifier_scores={}, matched_keywords=[],
        current_state="prompt",
        tool_name="", tool_input={}, prompt_id="", prompt_tools=[],
        session_prompt_ids=[], session_tools=OrderedDict(),
        gate_denied=False, gate_reason="",
        duration_ms=0.0, tool_result={},
        # tool_use_id="",
    )


def _config(session_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": session_id or "default"}}


def run_session(prompt: str, session_id: str = "", cwd: str = "") -> SessionState:
    """UserPromptSubmit entry point.

    Seeds a fresh state only when no checkpoint exists for this session yet.
    On subsequent turns the checkpoint supplies all prior state; we inject
    only the event-specific inputs on top.
    """
    graph = get_session_graph()
    cfg = _config(session_id)
    existing = graph.get_state(cfg)  # type: ignore[arg-type]
    state: SessionState = {**(existing.values if existing and existing.values else _fresh_state(session_id)), "event_type": "user_prompt_submit", "prompt": prompt, "cwd": cwd, "session_id": session_id}  # type: ignore[assignment]
    return graph.invoke(state, config=cfg)  # type: ignore[arg-type]


def run_gate(tool_name: str, tool_input: dict, session_id: str = "") -> dict:
    """PreToolUse entry point. Returns {gate_denied, gate_reason}.

    prompt_id flows from the checkpoint written by the prior user_prompt_submit.
    """
    cfg = _config(session_id)
    existing = get_session_graph().get_state(cfg)  # type: ignore[arg-type]
    state: SessionState = {**(existing.values if existing and existing.values else _fresh_state(session_id)), "event_type": "pre_tool_use", "tool_name": tool_name, "tool_input": tool_input, "session_id": session_id}  # type: ignore[assignment]
    result = get_session_graph().invoke(state, config=cfg)  # type: ignore[arg-type]
    return {"gate_denied": result["gate_denied"], "gate_reason": result["gate_reason"]}


def run_post_tool(tool_name: str, tool_input: dict, session_id: str,
                  duration_ms: float = 0.0, tool_result: dict | None = None,
                  prompt: str = "") -> None:
    """PostToolUse entry point.

    prompt_id flows from the checkpoint written by the prior user_prompt_submit.
    """
    cfg = _config(session_id)
    existing = get_session_graph().get_state(cfg)  # type: ignore[arg-type]
    state: SessionState = {**(existing.values if existing and existing.values else _fresh_state(session_id)), "event_type": "post_tool_use", "tool_name": tool_name, "tool_input": tool_input, "tool_result": tool_result or {}, "session_id": session_id, "duration_ms": duration_ms, "prompt": prompt}  # type: ignore[assignment]
    get_session_graph().invoke(state, config=cfg)  # type: ignore[arg-type]


def run_stop(session_id: str) -> None:
    """Stop hook entry point."""
    cfg = _config(session_id)
    existing = get_session_graph().get_state(cfg)  # type: ignore[arg-type]
    state: SessionState = {**(existing.values if existing and existing.values else _fresh_state(session_id)), "event_type": "stop", "session_id": session_id}  # type: ignore[assignment]
    get_session_graph().invoke(state, config=cfg)  # type: ignore[arg-type]

