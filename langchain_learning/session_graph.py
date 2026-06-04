"""Session State Machine — unified event graph.

One graph handles all four Claude Code hook events. Each event type routes to
its own chain of nodes, giving full observability across the session lifecycle
in a single graph topology.

Graph shape:

    START → route_event (conditional)
      ├── user_prompt_submit → load_turn → load_memories → load_session_context
      │                         → classify_domain → score_tools? → persist_session → END
      ├── pre_tool_use       → gate_check → END
      ├── post_tool_use      → log_tool_usage → END
      └── stop               → finalize_session → END

Node implementations live in langchain_learning/nodes/:
  prompt_nodes.py  — UserPromptSubmit chain
  tool_nodes.py    — PreToolUse + PostToolUse chains
  stop_nodes.py    — Stop chain
  registry.py      — NODE_REGISTRY + get_node() factory
"""
from __future__ import annotations

from pathlib import Path

from langgraph.graph import StateGraph, START, END

from langchain_learning.config import config as _cfg
from langchain_learning.nodes.registry import get_node
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

def build_session_graph():
    """Construct and compile the unified session graph."""
    builder = StateGraph(SessionState)

    # Register all nodes from registry
    for name in [
        "noop",
        "load_turn", "load_memories", "load_session_context",
        "classify_domain", "score_tools", "persist_session",
        "gate_check",
        "log_tool_usage",
        "finalize_session",
    ]:
        builder.add_node(name, get_node(name))

    # START → conditional route by event_type
    builder.add_conditional_edges(
        START,
        _route_event,
        {
            "user_prompt_submit": "load_turn",
            "pre_tool_use":       "gate_check",
            "post_tool_use":      "log_tool_usage",
            "stop":               "finalize_session",
            "unknown":            "noop",
        },
    )

    # UserPromptSubmit chain
    builder.add_edge("load_turn",            "load_memories")
    builder.add_edge("load_memories",        "load_session_context")
    builder.add_edge("load_session_context", "classify_domain")
    builder.add_conditional_edges(
        "classify_domain",
        _route_after_classify,
        {"score_tools": "score_tools", "skip_tools": "persist_session"},
    )
    builder.add_edge("score_tools",     "persist_session")
    builder.add_edge("persist_session", END)

    # PreToolUse chain
    builder.add_edge("gate_check",      END)

    # PostToolUse chain
    builder.add_edge("log_tool_usage",  END)

    # Stop chain
    builder.add_edge("finalize_session", END)

    # Fallback
    builder.add_edge("noop",            END)

    return builder.compile()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_graph = None


def get_session_graph():
    global _graph
    if _graph is None:
        _graph = build_session_graph()
    return _graph


# ---------------------------------------------------------------------------
# Public entry points (one per hook event)
# ---------------------------------------------------------------------------

def _blank_state() -> SessionState:
    return {
        "event_type": "", "prompt": "", "session_id": "", "turn": 0,
        "memories": [], "session_context": "", "session_context_ids": [],
        "domains": [], "keywords": [], "tool_hints": [], "skip_tools": False,
        "tool_name": "", "tool_input": {}, "prompt_id": "",
        "gate_denied": False, "gate_reason": "",
        "duration_ms": 0.0, "tool_use_id": "",
    }


def run_session(prompt: str, session_id: str = "", turn: int = 0) -> SessionState:
    """UserPromptSubmit entry point."""
    state = {**_blank_state(), "event_type": "user_prompt_submit",
             "prompt": prompt, "session_id": session_id, "turn": turn}
    return get_session_graph().invoke(state)


def run_gate(tool_name: str, tool_input: dict, prompt_id: str, session_id: str = "") -> dict:
    """PreToolUse entry point. Returns {gate_denied, gate_reason}."""
    state = {**_blank_state(), "event_type": "pre_tool_use",
             "tool_name": tool_name, "tool_input": tool_input,
             "prompt_id": prompt_id, "session_id": session_id}
    result = get_session_graph().invoke(state)
    return {"gate_denied": result["gate_denied"], "gate_reason": result["gate_reason"]}


def run_post_tool(tool_name: str, tool_input: dict, session_id: str, prompt_id: str,
                  tool_use_id: str = "", duration_ms: float = 0.0) -> None:
    """PostToolUse entry point."""
    state = {**_blank_state(), "event_type": "post_tool_use",
             "tool_name": tool_name, "tool_input": tool_input,
             "session_id": session_id, "prompt_id": prompt_id,
             "tool_use_id": tool_use_id, "duration_ms": duration_ms}
    get_session_graph().invoke(state)


def run_stop(session_id: str) -> None:
    """Stop hook entry point."""
    state = {**_blank_state(), "event_type": "stop", "session_id": session_id}
    get_session_graph().invoke(state)
