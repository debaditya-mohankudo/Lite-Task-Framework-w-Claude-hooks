"""FastAPI hook server — persistent process replacing per-invocation subprocess dispatcher.

Routes: POST /hook/{event} for UserPromptSubmit | PreToolUse | PostToolUse | Stop
State:  MemorySaver (in-process dict) replaces SqliteSaver — no SQLite checkpoint I/O.
        Single session at a time; evicted on Stop.
Launch: uvicorn hooks.server:app --host 127.0.0.1 --port 8766

Subprocess dispatcher (dispatcher.py) remains untouched for fallback / testing.
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

_PROJECT_ROOT = Path.home() / "workspace/claude-hooks"
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.logger import get_logger

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from langgraph.checkpoint.memory import MemorySaver
    import langchain_learning.session_graph as sg
    sg._graph = sg.build_session_graph(checkpointer=MemorySaver())
    log.info("hook-server: started, graph built with MemorySaver")
    yield
    log.info("hook-server: shutting down")


app = FastAPI(lifespan=lifespan)


def _evict_session(session_id: str) -> None:
    import langchain_learning.session_graph as sg
    if not session_id or not sg._graph:
        return
    try:
        checkpointer = sg._graph.checkpointer
        if hasattr(checkpointer, "storage"):
            checkpointer.storage.pop(session_id, None)
            log.info("evicted session=%s", session_id[:8])
    except Exception as exc:
        log.warning("eviction failed session=%s err=%s", session_id[:8], exc)


@app.post("/hook/UserPromptSubmit")
async def user_prompt_submit(request: Request):
    from hooks.dispatcher import _handle_user_prompt_submit
    body = await request.json()
    result = _handle_user_prompt_submit(body)
    return JSONResponse(content=result or {})


@app.post("/hook/PreToolUse")
async def pre_tool_use(request: Request):
    from hooks.dispatcher import _handle_pre_tool_use
    body = await request.json()
    result = _handle_pre_tool_use(body)
    return JSONResponse(content=result or {})


@app.post("/hook/PostToolUse")
async def post_tool_use(request: Request):
    from hooks.dispatcher import _handle_post_tool_use
    body = await request.json()
    result = _handle_post_tool_use(body)
    return JSONResponse(content=result or {})


@app.post("/hook/Stop")
async def stop(request: Request):
    from hooks.dispatcher import _handle_stop
    body = await request.json()
    result = _handle_stop(body)
    _evict_session(body.get("session_id", ""))
    return JSONResponse(content=result or {})


@app.get("/health")
async def health():
    import langchain_learning.session_graph as sg
    checkpointer = sg._graph.checkpointer if sg._graph else None
    sessions = len(getattr(checkpointer, "storage", {})) if checkpointer else 0
    return {"status": "ok", "sessions": sessions}
