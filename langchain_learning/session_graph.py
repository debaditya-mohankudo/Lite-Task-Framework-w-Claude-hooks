"""Session State Machine — unified event graph.

One graph handles all four Claude Code hook events. Each event type routes to
its own chain of nodes, giving full observability across the session lifecycle
in a single graph topology.

Graph shape:

    START → route_event (conditional)
      ├── user_prompt_submit → load_turn ──(task active?)──► load_active_task → load_task_history
      │                         → load_task_code (TurboVec RAG) → load_related_tasks ──► cwd_domain_detect → load_memories
      │                                            └─(no task)────────────►
      │                         → score_tools → set_prompt_id → log_task_events → END
      ├── pre_tool_use       → gate_check → END
      ├── post_tool_use      → log_tool_usage → update_tool_keywords → (tasks__set_active → activate_task | tasks__clear_active/finish → deactivate_task | *) → END
      └── stop               → noop → END

State persistence: the FastAPI hook server (hooks/server.py) holds a MemorySaver
(in-process dict) and passes it at startup via build_session_graph(checkpointer=...).
State is keyed by session_id (thread_id) and evicted on Stop. No SQLite I/O per node.

Node implementations live in langchain_learning/nodes/ — one class per file.
registry.py holds NODE_REGISTRY + get_node() factory.
"""
from __future__ import annotations

from collections import OrderedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import StateGraph, START, END

from langchain_learning.nodes.registry import get_node
from langchain_learning.nodes._node_log import wrap
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------

_EVENT_TYPES = {"user_prompt_submit", "pre_tool_use", "post_tool_use", "stop"}


def _route_event(state: SessionState) -> str:
    ev = state.get("event_type", "")
    return ev if ev in _EVENT_TYPES else "unknown"




# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_session_graph(checkpointer=None):
    """Construct and compile the unified session graph.

    Args:
        checkpointer: Optional LangGraph checkpointer (e.g. MemorySaver).
                      When provided, state is retained across invocations by thread_id.
                      When None (default/tests), each invoke is stateless.

    Tags: session-graph, LangGraph, StateGraph, event-routing, hooks
    """
    builder = StateGraph(SessionState)

    # Register all nodes from registry
    for name in [
        "noop",
        "load_turn", "load_active_task", "load_task_history", "load_task_code", "load_related_tasks", "load_memories",
        "cwd_domain_detect",
        "score_tools", "set_prompt_id",
        "gate_check",
        "log_tool_usage",
        "activate_task", "deactivate_task", "decision_task",
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
            "stop":               "noop",
            "unknown":            "noop",
        },
    )

    # UserPromptSubmit chain
    builder.add_conditional_edges(
        "load_turn",
        lambda s: "load_active_task" if s.get("active_task_id") else "load_related_tasks",
        {"load_active_task": "load_active_task", "load_related_tasks": "load_related_tasks"},
    )
    # fan-out from load_active_task: history, code, related run in parallel
    builder.add_edge("load_active_task",      "load_task_history")
    builder.add_edge("load_active_task",      "load_task_code")
    builder.add_edge("load_active_task",      "load_related_tasks")
    # fan-in at load_related_tasks → then second fan-out tier
    builder.add_edge("load_task_history",     "cwd_domain_detect")
    builder.add_edge("load_task_history",     "load_memories")
    builder.add_edge("load_task_history",     "score_tools")
    builder.add_edge("load_task_code",        "cwd_domain_detect")
    builder.add_edge("load_task_code",        "load_memories")
    builder.add_edge("load_task_code",        "score_tools")
    builder.add_edge("load_related_tasks",    "cwd_domain_detect")
    builder.add_edge("load_related_tasks",    "load_memories")
    builder.add_edge("load_related_tasks",    "score_tools")
    # fan-in: all three converge at set_prompt_id
    builder.add_edge("cwd_domain_detect",     "set_prompt_id")
    builder.add_edge("load_memories",         "set_prompt_id")
    builder.add_edge("score_tools",           "set_prompt_id")
    builder.add_edge("set_prompt_id",   "log_task_events")
    builder.add_edge("log_task_events", END)

    # PreToolUse chain
    builder.add_edge("gate_check", END)

    # PostToolUse chain
    def _post_tool_route(state: SessionState) -> str:
        tool = state.get("tool_name", "")
        if tool in ("tasks__set_active", "tasks__pop_active"):
            return "activate_task"
        if tool in ("tasks__clear_active", "tasks__finish"):
            return "deactivate_task"
        if tool == "tasks__add_decision":
            return "decision_task"
        return END

    builder.add_conditional_edges(
        "log_tool_usage",
        _post_tool_route,
        {"activate_task": "activate_task", "deactivate_task": "deactivate_task",
         "decision_task": "decision_task", END: END},
    )
    builder.add_edge("activate_task",   END)
    builder.add_edge("deactivate_task", END)
    builder.add_edge("decision_task",   END)

    # Fallback
    builder.add_edge("noop",            END)

    return builder.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_graph = None


def get_session_graph():
    """Return the module-level graph.

    In production, _graph is set by the FastAPI server lifespan (MemorySaver).
    In tests, callers may inject _graph directly, or this falls back to a
    fresh MemorySaver graph per process.
    """
    global _graph
    if _graph is None:
        from langgraph.checkpoint.memory import MemorySaver
        _graph = build_session_graph(checkpointer=MemorySaver())
    return _graph


# ---------------------------------------------------------------------------
# Public entry points (one per hook event)
# ---------------------------------------------------------------------------

def _fresh_state(session_id: str) -> SessionState:
    """Full default state for the very first user_prompt_submit of a new session."""
    return SessionState(
        event_type="", prompt="", cwd="", session_id=session_id,
        turn=0,
        memories=[],
        domains=[], keywords=[], tool_hints=[],
        active_task_id="", active_task_title="", task_memories=[], task_context=[], task_rag_chunks=[], task_stack=[], mid_task_decisions=[], related_tasks=[],
        current_state="prompt",
        tool_name="", tool_input={}, prompt_id="", prompt_tools=[],
        session_prompt_ids=[], session_tools=OrderedDict(), session_prompt_texts={},
        gate_denied=False, gate_reason="",
        duration_ms=0.0, tool_result={},
        # tool_use_id="",
    )


def _config(session_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": session_id or "default"}}


def _base_state(session_id: str) -> SessionState:
    """Return checkpoint state for session_id, or a fresh state if none exists."""
    cfg = _config(session_id)
    existing = get_session_graph().get_state(cfg)  # type: ignore[arg-type]
    return existing.values if existing and existing.values else _fresh_state(session_id)  # type: ignore[return-value]


def run_session(prompt: str, session_id: str = "", cwd: str = "") -> SessionState:
    """UserPromptSubmit entry point.

    Seeds a fresh state only when no checkpoint exists for this session yet.
    On subsequent turns the checkpoint supplies all prior state; we inject
    only the event-specific inputs on top.
    """
    import time as _time
    t0 = _time.monotonic()
    state: SessionState = _base_state(session_id) | {"event_type": "user_prompt_submit", "prompt": prompt, "cwd": cwd, "session_id": session_id}  # type: ignore[operator]
    result = get_session_graph().invoke(state, config=_config(session_id))  # type: ignore[arg-type]
    _log.info("UPS phase=done session=%s elapsed_ms=%.0f", (session_id or "")[:8], ((_time.monotonic() - t0) * 1000))
    return result


def run_gate(tool_name: str, tool_input: dict, session_id: str = "") -> dict:
    """PreToolUse entry point. Returns {gate_denied, gate_reason}.

    prompt_id flows from the checkpoint written by the prior user_prompt_submit.
    """
    cfg = _config(session_id)
    try:
        saved = get_session_graph().get_state(cfg)
        prompt = (saved.values.get("prompt") or "") if saved and saved.values else ""
    except Exception:
        prompt = ""
    state: SessionState = _base_state(session_id) | {"event_type": "pre_tool_use", "tool_name": tool_name, "tool_input": tool_input, "session_id": session_id, "prompt": prompt}  # type: ignore[operator]
    result = get_session_graph().invoke(state, config=cfg)  # type: ignore[arg-type]
    get_session_graph().update_state(cfg, {"gate_denied": False, "gate_reason": "", "tool_name": "", "tool_input": {}})
    return {"gate_denied": result["gate_denied"], "gate_reason": result["gate_reason"]}


def run_post_tool(tool_name: str, tool_input: dict, session_id: str,
                  duration_ms: float = 0.0, tool_result: dict | None = None,
                  prompt: str = "") -> None:
    """PostToolUse entry point.

    prompt_id flows from the checkpoint written by the prior user_prompt_submit.
    """
    cfg = _config(session_id)
    state: SessionState = _base_state(session_id) | {"event_type": "post_tool_use", "tool_name": tool_name, "tool_input": tool_input, "tool_result": tool_result or {}, "session_id": session_id, "duration_ms": duration_ms, "prompt": prompt}  # type: ignore[operator]
    get_session_graph().invoke(state, config=cfg)  # type: ignore[arg-type]
    get_session_graph().update_state(cfg, {"tool_name": "", "tool_input": {}, "tool_result": {}, "duration_ms": 0.0})


def run_stop(session_id: str) -> None:
    """Stop hook entry point."""
    cfg = _config(session_id)
    state: SessionState = _base_state(session_id) | {"event_type": "stop", "session_id": session_id}  # type: ignore[operator]
    get_session_graph().invoke(state, config=cfg)  # type: ignore[arg-type]
    get_session_graph().update_state(cfg, {
        "event_type": "",
        "prompt": "", "prompt_id": "", "prompt_tools": [],
        "tool_name": "", "tool_input": {}, "tool_result": {}, "duration_ms": 0.0,
        "gate_denied": False, "gate_reason": "",
    })

