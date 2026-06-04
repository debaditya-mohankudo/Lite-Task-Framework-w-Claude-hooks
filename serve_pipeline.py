"""Process 1 — LangServe pipeline server.

Wraps the LangGraph session graph as a FastAPI app with LangServe-generated
/pipeline/invoke, /pipeline/stream, /pipeline/batch endpoints.

Usage:
    uv run uvicorn serve_pipeline:app --host 127.0.0.1 --port 8766 --reload

Endpoints:
    GET  /health                       → {"status": "ok"}
    POST /pipeline/invoke              → full SessionState in → SessionState out
    POST /pipeline/stream              → streaming chunks
    POST /pipeline/batch               → batch of prompts
    GET  /pipeline/playground          → LangServe interactive UI
    POST /run                          → thin wrapper: {prompt, session_id, turn} only
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent / "src"))

from fastapi import FastAPI
from langserve import add_routes
from pydantic import BaseModel, Field

from langchain_learning.session_graph import build_session_graph, SessionState

app = FastAPI(
    title="claude-hooks-pipeline",
    version="0.1.0",
    description="LangServe wrapper for the claude-hooks LangGraph session pipeline.",
)

# Build graph once at import time — CompiledStateGraph is thread-safe for concurrent invocations.
_graph = build_session_graph()


@app.get("/health")
def health():
    return {"status": "ok", "server": "claude-hooks-pipeline", "port": 8766}


# ---------------------------------------------------------------------------
# Thin /run endpoint — hooks only need to send prompt + session_id + turn.
# Fills SessionState defaults so callers don't need to know the full schema.
# ---------------------------------------------------------------------------

class RunRequest(BaseModel):
    prompt: str
    session_id: str = ""
    turn: int = Field(default=0, ge=0)


@app.post("/run")
def run(req: RunRequest) -> dict:
    """Invoke the pipeline with minimal input. Returns full SessionState."""
    initial: SessionState = {
        "prompt":              req.prompt,
        "session_id":          req.session_id,
        "turn":                req.turn,
        "memories":            [],
        "session_context":     "",
        "session_context_ids": [],
        "domains":             [],
        "keywords":            [],
        "tool_hints":          [],
        "skip_tools":          False,
    }
    return _graph.invoke(initial)


# ---------------------------------------------------------------------------
# Full LangServe routes — exposes raw graph with complete SessionState schema.
# Useful for testing, playground, and future integrations.
# ---------------------------------------------------------------------------

add_routes(
    app,
    _graph,
    path="/pipeline",
    enabled_endpoints=["invoke", "batch", "stream", "playground"],
)
