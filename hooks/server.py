"""FastAPI hook server — persistent process replacing per-invocation subprocess dispatcher.

Routes: POST /hook/{event} for UserPromptSubmit | PreToolUse | PostToolUse | Stop
State:  MemorySaver (in-process dict) replaces SqliteSaver — no SQLite checkpoint I/O.
        Single session at a time; evicted on Stop.
Launch: uvicorn hooks.server:app --host 127.0.0.1 --port 8766
        (no --reload: it restarts the worker on every edit, wiping the MemorySaver
         checkpoint and active-task context; run it manually when developing)

Subprocess dispatcher (dispatcher.py) remains untouched for fallback / testing.
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

_PROJECT_ROOT = Path.home() / "workspace/claude-hooks"
_HOOKS_DIR = _PROJECT_ROOT / "hooks"
for _p in (str(_PROJECT_ROOT), str(_HOOKS_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import time

from fastapi import FastAPI, Form, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.logger import get_logger, setup

log = get_logger(__name__)
_slog = setup("server")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from langgraph.checkpoint.memory import MemorySaver
    import langchain_learning.session_graph as sg
    sg._graph = sg.build_session_graph(checkpointer=MemorySaver())
    import hooks.server_memory as server_memory
    server_memory.load()  # hydrate the in-memory session from durable SQLite (survives reloads)
    log.info("hook-server: started, graph built with MemorySaver, server_session=%s", server_memory.SERVER_SESSION_ID)
    yield
    log.info("hook-server: shutting down")


import jinja2 as _jinja2

_JINJA_ENV = _jinja2.Environment(
    loader=_jinja2.FileSystemLoader(str(_HOOKS_DIR / "templates")),
    autoescape=True,
)


def _render(template_name: str, **ctx) -> HTMLResponse:
    t = _JINJA_ENV.get_template(template_name)
    return HTMLResponse(t.render(**ctx))


def _error_partial(message: str, detail: str = "") -> HTMLResponse:
    return _render("ui/partials/error.html", message=message, detail=detail)


app = FastAPI(lifespan=lifespan)
app.mount("/ui/static", StaticFiles(directory=str(_HOOKS_DIR / "static")), name="ui-static")


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if request.url.path.startswith("/ui"):
        return _error_partial(f"HTTP {exc.status_code}", str(exc.detail))
    return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    log.error("unhandled exception: %s", exc, exc_info=True)
    if request.url.path.startswith("/ui"):
        return _error_partial("Something went wrong", str(exc))
    return JSONResponse({"detail": "Internal server error"}, status_code=500)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log method, path, status, and elapsed ms for every request via the bare 'server' logger."""
    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed = int((time.perf_counter() - t0) * 1000)
    _slog.info("HTTP %s %s → %d  %dms", request.method, request.url.path, response.status_code, elapsed)
    return response


def _evict_session(session_id: str) -> None:
    """Remove session from MemorySaver checkpoint storage on SessionEnd. No-op if session unknown.

    NOT called on Stop — Stop fires every assistant turn, so evicting there wipes
    cross-turn checkpoint state (active task, turn counter). Eviction belongs to the
    real session-close signal (SessionEnd). See bug:b7cb4eb4.
    """
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
    """UserPromptSubmit hook — runs the full UPS LangGraph chain.

    Injects memories, tool hints, domain classification, and task context into the session
    checkpoint. Returns hookSpecificOutput.additionalSystemPrompt for Claude to consume.
    All lc.* node logs write immediately to claude_hooks.sqlite via SQLiteHandler.
    """
    from hooks.dispatcher import _handle_user_prompt_submit, _extract_prompt
    body = await request.json()
    result = _handle_user_prompt_submit(body)
    try:
        import hooks.server_memory as server_memory
        server_memory.record_prompt(body.get("session_id", ""), _extract_prompt(body))
    except Exception as exc:
        log.warning("server_memory: record_prompt failed: %s", exc)
    return JSONResponse(content=result or {})


@app.post("/hook/PreToolUse")
async def pre_tool_use(request: Request):
    """PreToolUse hook — runs gate_check node against the current session checkpoint.

    Returns permissionDecision=deny with a reason if a gated tool (e.g. imessage__send)
    is called without its prereq (e.g. contacts__search) in the session. Returns {} to
    allow. Gate internals (name_arg_check, ALLOW/DENY rows, prompt_id correlation)
    write immediately to claude_hooks.sqlite via SQLiteHandler.
    """
    from hooks.dispatcher import _handle_pre_tool_use
    body = await request.json()
    result = _handle_pre_tool_use(body)
    return JSONResponse(content=result or {})


@app.post("/hook/PostToolUse")
async def post_tool_use(request: Request):
    """PostToolUse hook — runs log_tool_usage node and conditional task-lifecycle bridge nodes.

    Upserts tool hint row in tool_hints.sqlite (skipped for test sessions).
    Bridge nodes fire when tool_name matches a lifecycle tool (tasks__set_active,
    tasks__pop_active, tasks__clear_active, tasks__finish, tasks__add_decision) —
    they write task activation state into the MemorySaver checkpoint so the next
    UPS turn sees the updated active task. Always returns {}.
    """
    from hooks.dispatcher import _handle_post_tool_use
    body = await request.json()
    result = _handle_post_tool_use(body)
    try:
        import hooks.server_memory as server_memory
        server_memory.record_tool_from_hook(body)
        server_memory.record_task_from_hook(body)
    except Exception as exc:
        log.warning("server_memory: record failed: %s", exc)
    return JSONResponse(content=result or {})


@app.post("/hook/Stop")
async def stop(request: Request):
    """Stop hook — finalises the *turn*, NOT the session.

    Fires at the end of every assistant response. Clears per-turn ephemeral fields
    (via run_stop) but must NOT evict the checkpoint — that would wipe cross-turn
    state (active task, turn counter) every turn. Session eviction happens on
    SessionEnd. Always returns {}.
    """
    from hooks.dispatcher import _handle_stop
    body = await request.json()
    result = _handle_stop(body)
    return JSONResponse(content=result or {})


@app.post("/hook/SessionEnd")
async def session_end(request: Request):
    """SessionEnd hook — the session has actually closed; evict its checkpoint.

    This is the correct place to reclaim MemorySaver storage (fires once when the
    session ends, unlike Stop which fires every turn). Always returns {}.
    """
    body = await request.json()
    _evict_session(body.get("session_id", ""))
    return JSONResponse(content={})


@app.get("/health")
async def health():
    """Health check — returns status=ok and current active session count from MemorySaver."""
    import langchain_learning.session_graph as sg
    checkpointer = sg._graph.checkpointer if sg._graph else None
    sessions = len(getattr(checkpointer, "storage", {})) if checkpointer else 0
    return {"status": "ok", "sessions": sessions}


@app.get("/session/memory")
async def session_memory(n_prompts: int = 20, m_tasks: int = 10, k_tools: int = 30, n_events: int = 50):
    """Server session memory — per-kind windows plus a unified event sequence.

    Read-only consolidated context ("what was I working on?"): recent prompts,
    tasks, tool calls, and a chronological `events` timeline. Durable across
    reloads (SQLite-backed), capped to a rolling window.
    """
    import hooks.server_memory as server_memory
    return server_memory.get_server_memory(n_prompts=n_prompts, m_tasks=m_tasks, k_tools=k_tools, n_events=n_events)


_BODY_FIELDS = ("Type", "Task", "Motivation", "Resolution", "Files", "Notes", "Next")
_BODY_FIELD_RE = None  # built lazily


def _parse_body_fields(body: str) -> list[dict] | None:
    """Parse 'Field: value' structured body into a list of {label, value, is_code} dicts.

    Returns None if the body doesn't look structured (no recognised field found).
    Detects fenced code blocks (``` ... ```) within values and marks them is_code=True.
    """
    import re
    global _BODY_FIELD_RE
    if _BODY_FIELD_RE is None:
        pattern = r"^(" + "|".join(_BODY_FIELDS) + r"):\s*"
        _BODY_FIELD_RE = re.compile(pattern, re.MULTILINE)

    matches = list(_BODY_FIELD_RE.finditer(body))
    if not matches:
        return None

    fields = []
    for i, m in enumerate(matches):
        label = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        raw_value = body[start:end].strip()

        # Detect fenced code block
        code_match = re.match(r"^```(\w*)\n(.*?)```\s*$", raw_value, re.DOTALL)
        if code_match:
            fields.append({"label": label, "value": code_match.group(2).rstrip(), "is_code": True})
        else:
            fields.append({"label": label, "value": raw_value, "is_code": False})

    return fields if fields else None


def _valid_status(s: str) -> str:
    return s if s in ("open", "done") else "open"


def _get_active_session() -> dict:
    """Scan MemorySaver for any active task. Returns dict with task_id, title, session_id, turn or empty dict."""
    try:
        import langchain_learning.session_graph as sg
        storage = getattr(getattr(sg._graph, "checkpointer", None), "storage", {})
        for sid, data in storage.items():
            state = next(iter(data.values()), {}).get("channel_values", {}) if data else {}
            task_id = state.get("active_task_id", "")
            if task_id:
                return {
                    "task_id": task_id,
                    "title": state.get("active_task_title", ""),
                    "session_id": sid,
                    "turn": state.get("turn", 0),
                }
    except Exception:
        pass
    return {}


@app.get("/ui/", response_class=HTMLResponse)
async def ui_index(request: Request, status: str = "open"):
    """Task Manager UI — full two-panel layout."""
    from src.tools.tasks import handle_list
    status = _valid_status(status)
    tasks = handle_list(status=status)
    return _render("ui/index.html", tasks=tasks, status=status)


@app.get("/ui/tasks/body-fields", response_class=HTMLResponse)
async def ui_body_fields(issue_type: str = "task"):
    """Returns dynamic body fields partial based on issue_type — swapped by HTMX on type change."""
    return _render("ui/partials/task_body_fields.html", issue_type=issue_type)


@app.get("/ui/tasks/new", response_class=HTMLResponse)
async def ui_task_new(request: Request):
    """Create task form partial."""
    from src.tools.tasks import handle_list
    epics = [t for t in handle_list(status="open") if t.get("issue_type") in ("epic", "story")]
    return _render("ui/partials/create_form.html", epics=epics, error="", issue_type="task")


@app.get("/ui/tasks/{task_id}", response_class=HTMLResponse)
async def ui_task_detail(task_id: str):
    """Task detail partial — swapped into #detail-panel by HTMX."""
    import sys as _sys
    if "src" not in _sys.path:
        _sys.path.insert(0, str(_PROJECT_ROOT / "src"))
    from src.tools.tasks import handle_get, handle_history, handle_neighbors

    task = handle_get(task_id)
    if "error" in task:
        return HTMLResponse(f"<div class='empty-state'>Task not found: {task_id}</div>")

    history = handle_history(task_id)
    turns     = [e for e in history if e.get("tools") != "decision"]
    decisions = [e for e in history if e.get("tools") == "decision"]

    # Group turns by session for cross-session collapse
    from collections import OrderedDict
    _session_groups: OrderedDict = OrderedDict()
    for ev in turns:
        sid = ev.get("session_id") or "unknown"
        _session_groups.setdefault(sid, []).append(ev)
    turn_sessions = [
        {"session_id": sid, "events": evts, "is_current": False}
        for sid, evts in _session_groups.items()
    ]
    if turn_sessions:
        turn_sessions[-1]["is_current"] = True
        # mark the very last event across all sessions
        if turn_sessions[-1]["events"]:
            turn_sessions[-1]["events"][-1]["is_latest"] = True

    try:
        neighbors = handle_neighbors(task_id)
    except Exception:
        neighbors = []

    # parent task
    parent = None
    if task.get("parent_id"):
        p = handle_get(task["parent_id"])
        if "error" not in p:
            parent = p

    # live session check — scan MemorySaver for this task as active
    live_session = None
    live_turn = 0
    try:
        import langchain_learning.session_graph as sg
        storage = getattr(getattr(sg._graph, "checkpointer", None), "storage", {})
        for _sid, data in storage.items():
            state = next(iter(data.values()), {}).get("channel_values", {}) if data else {}
            if state.get("active_task_id") == task_id:
                live_session = _sid
                live_turn = state.get("turn", 0)
                break
    except Exception:
        pass

    # split tags into structured (prefixed) vs plain labels
    _STRUCTURED_PREFIXES = ("parent:", "project:", "domain:", "frozen")
    all_tags = [t.strip() for t in (task.get("tags") or "").split(",") if t.strip()]
    structured_tags = [t for t in all_tags if any(t.startswith(p) or t == p for p in _STRUCTURED_PREFIXES)]
    label_tags = [t for t in all_tags if t not in structured_tags]

    body_fields = _parse_body_fields(task.get("body") or "")

    # Parse review checklist if this is a review task
    import json as _json
    review_items: list[dict] = []
    if task.get("issue_type") == "review" and task.get("review_result"):
        try:
            review_items = _json.loads(task["review_result"])
        except Exception:
            pass

    return _render("ui/partials/task_detail.html",
                   task=task, turns=turns, decisions=decisions,
                   turn_sessions=turn_sessions,
                   neighbors=neighbors, parent=parent,
                   live_session=live_session, live_turn=live_turn,
                   structured_tags=structured_tags, label_tags=label_tags,
                   body_fields=body_fields, review_items=review_items)


@app.post("/ui/tasks", response_class=HTMLResponse)
async def ui_task_create(
    title: str = Form(...),
    body_task: str = Form(""),
    body_motivation: str = Form(""),
    body_resolution: str = Form(""),
    issue_type: str = Form("task"),
    parent_id: str = Form(""),
):
    """Create a task via the web form. On success, returns refreshed sidebar partial."""
    from src.tools.tasks import handle_create, handle_list
    parts = [f"Type: feature"]
    if body_task:       parts.append(f"\nTask: {body_task.strip()}")
    if body_motivation: parts.append(f"\nMotivation: {body_motivation.strip()}")
    if body_resolution: parts.append(f"\nResolution: {body_resolution.strip()}")
    body = "\n".join(parts)

    from hooks.gates import validate_jira_hierarchy
    error = validate_jira_hierarchy(issue_type, parent_id)
    if not error:
        result = handle_create(
            title=title, body=body, issue_type=issue_type,
            parent_id=parent_id, cwd=str(_PROJECT_ROOT),
        )
        error = result.get("error")
    if error:
        epics = [t for t in handle_list(status="open") if t.get("issue_type") in ("epic", "story")]
        return _render("ui/partials/create_form.html", epics=epics, error=result["error"])
    # success — return refreshed sidebar
    tasks = handle_list(status="open")
    return _render("ui/partials/sidebar.html", tasks=tasks, status="open")


@app.get("/ui/sidebar", response_class=HTMLResponse)
async def ui_sidebar(request: Request, status: str = "open"):
    """Sidebar partial — returned by HTMX status-tab clicks."""
    from src.tools.tasks import handle_list
    status = _valid_status(status)
    tasks = handle_list(status=status)
    active = _get_active_session()
    return _render("ui/partials/sidebar.html", tasks=tasks, status=status,
                   active_task_id=active.get("task_id", ""))


@app.get("/ui/cockpit", response_class=HTMLResponse)
async def ui_cockpit():
    """Cockpit strip partial — polled every 10s by base.html."""
    active = _get_active_session()
    return _render("ui/partials/cockpit.html", active=active)


@app.get("/ui/search", response_class=HTMLResponse)
async def ui_search(q: str = ""):
    """Search partial — tasks + decisions grouped results for the search overlay."""
    from src.tools.tasks import handle_search, _connect
    q = q.strip()
    if len(q) < 2:
        return HTMLResponse("")
    raw = handle_search(q, status="open,done,wip,abandoned")[:12]
    for t in raw:
        tags = (t.get("tags") or "").split(",")
        t["project"] = next((tag.replace("project:", "") for tag in tags if tag.startswith("project:")), "")
    tasks = raw
    with _connect() as conn:
        decisions = conn.execute(
            """SELECT e.summary, e.turn, e.logged_at, e.task_id, t.title as task_title
               FROM task_events e
               LEFT JOIN open_tasks t ON t.id = e.task_id
               WHERE e.tools = 'decision' AND lower(e.summary) LIKE lower(?)
               ORDER BY e.logged_at DESC LIMIT 6""",
            (f"%{q}%",),
        ).fetchall()
        decisions = [dict(d) for d in decisions]
    import sqlite3 as _sqlite3, os as _os
    mem_db = _os.path.expanduser("~/.claude/MEMORY.sqlite")
    memories = []
    if _os.path.exists(mem_db):
        with _sqlite3.connect(mem_db) as mconn:
            mconn.row_factory = _sqlite3.Row
            memories = [dict(r) for r in mconn.execute(
                """SELECT name, type, domain, priority, body
                   FROM memories
                   WHERE lower(body) LIKE lower(?) OR lower(name) LIKE lower(?)
                   ORDER BY priority ASC LIMIT 3""",
                (f"%{q}%", f"%{q}%"),
            ).fetchall()]
    return _render("ui/partials/search_results.html", q=q, tasks=tasks, decisions=decisions, memories=memories)


@app.get("/session")
async def session():
    """Session list — returns all active sessions with turn counts from MemorySaver storage."""
    import langchain_learning.session_graph as sg
    checkpointer = sg._graph.checkpointer if sg._graph else None
    storage = getattr(checkpointer, "storage", {})
    sessions = [{"session_id": sid, "turns": len(data)} for sid, data in storage.items()]
    return {"count": len(sessions), "sessions": sessions}
