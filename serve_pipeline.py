"""Stateful session server — LangGraph + MemorySaver checkpointer.

One graph instance, one MemorySaver, shared across all requests.
session_id maps to LangGraph thread_id — state is retained in memory
across all four hook events for the lifetime of a session.

Lifecycle:
    POST /session/prompt    — UserPromptSubmit: runs classify chain, stores state
    POST /session/pre_tool  — PreToolUse: restores state, runs gate_check
    POST /session/post_tool — PostToolUse: restores state, runs log_tool_usage
    POST /session/stop      — Stop: restores state, finalizes + flushes to sessions.db

Usage:
    uv run uvicorn serve_pipeline:app --host 127.0.0.1 --port 8766 --reload
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "src"))
sys.path.insert(0, str(Path(__file__).parent / "hooks"))

from fastapi import FastAPI
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel, Field

from langchain_learning.session_graph import build_session_graph, _blank_state
from src.logger import get_logger

_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# One graph + one checkpointer — shared for the server lifetime
# ---------------------------------------------------------------------------

_checkpointer = MemorySaver()
_graph        = build_session_graph(checkpointer=_checkpointer)


def _config(session_id: str) -> dict:
    """LangGraph config that scopes state to a session."""
    return {"configurable": {"thread_id": session_id}}


app = FastAPI(
    title="claude-hooks-session-server",
    version="0.2.0",
    description="Stateful LangGraph session server for claude-hooks.",
)


@app.get("/health")
def health():
    return {"status": "ok", "server": "claude-hooks-session-server", "port": 8766}


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class PromptRequest(BaseModel):
    prompt:     str
    session_id: str = ""
    cwd:        str = ""


class PreToolRequest(BaseModel):
    session_id: str
    tool_name:  str
    tool_input: dict = Field(default_factory=dict)
    prompt_id:  str  = ""


class PostToolRequest(BaseModel):
    session_id:  str
    tool_name:   str
    tool_input:  dict  = Field(default_factory=dict)
    tool_use_id: str   = ""
    duration_ms: float = 0.0


class StopRequest(BaseModel):
    session_id: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/session/prompt")
def session_prompt(req: PromptRequest) -> dict:
    """UserPromptSubmit — classify, score tools, set prompt_id. State stored in checkpointer."""
    state = {
        **_blank_state(),
        "event_type": "user_prompt_submit",
        "prompt":     req.prompt,
        "cwd":        req.cwd,
        "session_id": req.session_id,
    }
    result = _graph.invoke(state, config=_config(req.session_id))
    _log.info("prompt: session=%s domains=%s tools=%d",
              req.session_id[:8], result.get("domains"), len(result.get("tool_hints", [])))
    return result


@app.post("/session/pre_tool")
def session_pre_tool(req: PreToolRequest) -> dict:
    """PreToolUse — restores checkpoint state, overlays event fields, runs gate_check."""
    # Only pass event-specific fields — checkpointer provides the rest from prior invoke
    state = {
        "event_type": "pre_tool_use",
        "tool_name":  req.tool_name,
        "tool_input": req.tool_input,
        "gate_denied": False,
        "gate_reason": "",
    }
    result = _graph.invoke(state, config=_config(req.session_id))
    _log.info("pre_tool: session=%s tool=%s denied=%s",
              req.session_id[:8], req.tool_name, result.get("gate_denied"))
    return {"gate_denied": result["gate_denied"], "gate_reason": result["gate_reason"]}


@app.post("/session/post_tool")
def session_post_tool(req: PostToolRequest) -> dict:
    """PostToolUse — restores checkpoint state, overlays event fields, logs tool usage."""
    state = {
        "event_type":  "post_tool_use",
        "tool_name":   req.tool_name,
        "tool_input":  req.tool_input,
        "tool_use_id": req.tool_use_id,
        "duration_ms": req.duration_ms,
    }
    _graph.invoke(state, config=_config(req.session_id))
    _log.info("post_tool: session=%s tool=%s", req.session_id[:8], req.tool_name)
    return {}


@app.post("/session/stop")
def session_stop(req: StopRequest) -> dict:
    """Stop — restores checkpoint state, finalizes keywords, flushes to sessions.db."""
    state = {"event_type": "stop"}
    _graph.invoke(state, config=_config(req.session_id))
    _log.info("stop: session=%s — flushed to sessions.db", req.session_id[:8])
    return {}
