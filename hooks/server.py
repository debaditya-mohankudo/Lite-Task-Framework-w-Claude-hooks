"""FastAPI hook server — persistent process replacing per-invocation subprocess dispatcher.

Routes: POST /hook/{event} for UserPromptSubmit | PreToolUse | PostToolUse | Stop | SessionStart | SessionEnd
State:  SqliteSaver (~/.claude/langgraph_checkpoints.db) — durable, survives reloads.
Launch: uvicorn hooks.server:app --host 127.0.0.1 --port 8766

Subprocess dispatcher (dispatcher.py) remains untouched for fallback / testing.
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

from hooks.paths import PROJECT_ROOT as _PROJECT_ROOT, HOOKS_DIR as _HOOKS_DIR
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


_CHECKPOINT_DB = Path.home() / ".claude" / "langgraph_checkpoints.db"


_CHECKPOINT_SESSION_CAP = 5

# Global cap on total checkpoint rows across all kept threads — whichever-first
# with session_cap. Cross-thread eviction (session_cap) only removes whole
# inactive threads; nothing previously bounded how many checkpoint VERSIONS a
# single long-running active thread accumulates (one row per graph step,
# forever). LangGraph's SqliteSaver stores a full channel_values snapshot per
# checkpoint (not a diff), so pruning older versions only costs time-travel/
# resume-from-old-turn ability, never current-state correctness.
_CHECKPOINT_ROW_CAP = 1000


def _trim_checkpoints(
    db_path: Path,
    session_cap: int = _CHECKPOINT_SESSION_CAP,
    row_cap: int = _CHECKPOINT_ROW_CAP,
) -> None:
    """Keep only the most recently active sessions AND cap total checkpoint rows.

    Runs at server startup AND on every UserPromptSubmit. Two independent caps,
    whichever binds first:
    - session_cap: threads ranked by latest rowid — top session_cap kept, rest evicted
    - row_cap: total checkpoint rows across all kept threads, oldest-first pruned

    Also cleans up ALL orphaned `writes` rows (any tuple no longer present in
    `checkpoints`) — not just ones evicted by this call. Discovered 2026-07-05
    (task:029d614f) that a single long-running session had grown
    langgraph_checkpoints.db to 1.68GB (5976 checkpoint rows, 447k writes rows,
    only 30 of which mapped to any surviving checkpoint even before this fix),
    causing get_current_session()'s checkpointer.list() to hang for 10s+.
    """
    import sqlite3
    try:
        conn = sqlite3.connect(str(db_path), timeout=5)
        try:
            threads = conn.execute(
                "SELECT thread_id FROM checkpoints GROUP BY thread_id "
                "ORDER BY MAX(rowid) DESC"
            ).fetchall()
            keep = [r[0] for r in threads[:session_cap]]
            evict = [r[0] for r in threads[session_cap:]]
            if evict:
                placeholders = ",".join("?" * len(evict))
                conn.execute(f"DELETE FROM checkpoints WHERE thread_id IN ({placeholders})", evict)
                short_ids = [s[:8] for s in evict]
                log.info("checkpoint trim: kept %d sessions, evicted %d (%s)", len(keep), len(evict), short_ids)

            total = conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
            if total > row_cap:
                stale = conn.execute(
                    "SELECT thread_id, checkpoint_ns, checkpoint_id FROM checkpoints "
                    "ORDER BY rowid DESC LIMIT -1 OFFSET ?",
                    (row_cap,),
                ).fetchall()
                conn.executemany(
                    "DELETE FROM checkpoints WHERE thread_id=? AND checkpoint_ns=? AND checkpoint_id=?",
                    stale,
                )
                log.info("checkpoint trim: row cap exceeded (%d > %d), pruned %d checkpoint version(s)",
                          total, row_cap, len(stale))

            # Clean up ALL orphaned writes rows — including pre-existing orphans
            # never tied to a checkpoint this call evicted, not just fresh ones.
            cur = conn.execute(
                "DELETE FROM writes WHERE (thread_id, checkpoint_ns, checkpoint_id) NOT IN "
                "(SELECT thread_id, checkpoint_ns, checkpoint_id FROM checkpoints)"
            )
            if cur.rowcount:
                log.info("checkpoint trim: removed %d orphaned/stale writes row(s)", cur.rowcount)

            conn.commit()
        finally:
            conn.close()
    except Exception as exc:
        log.warning("checkpoint trim failed: %s", exc)


def _compact_checkpoint_db(db_path: Path) -> None:
    """Reclaim disk space by copying to :memory:, VACUUMing there, then
    atomically swapping in a fresh compact file.

    SAFE TO CALL ONLY when no other connection holds db_path open — e.g. at
    server startup, BEFORE the persistent SqliteSaver connection is opened.
    Swapping the file while a live connection is open would orphan future
    writes to the old (renamed-away) inode, since SQLite connections hold
    their file open by inode, not by path. Do NOT call this from
    _trim_checkpoints() or anywhere else in the per-UserPromptSubmit hot path.

    Row-count capping (_trim_checkpoints) bounds row COUNT on every prompt;
    this bounds actual disk usage, but only safely at points where nothing
    else has the file open — hence startup-only, not hot-path.

    Discovered 2026-07-05 (task:5afd1b61): a single long conversation grew
    this file to 1.68GB even after row-capping shipped, because DELETE alone
    doesn't shrink a SQLite file — freed pages stay allocated until VACUUM.
    Manually verified this exact copy-to-memory-then-vacuum approach on the
    live file: 1.7GB -> 317MB in under 7 seconds, PRAGMA integrity_check ok.
    """
    import os as _os_mod
    import sqlite3
    import time as _time_mod

    if not db_path.exists():
        return

    tmp_path = db_path.with_name(db_path.name + ".compact-tmp")
    try:
        t0 = _time_mod.time()
        log.info("checkpoint compact[pid=%d]: begin, opening %s for backup-to-memory", _os_mod.getpid(), db_path)
        src = sqlite3.connect(str(db_path))
        mem = sqlite3.connect(":memory:")
        src.backup(mem)
        src.close()

        mem.execute("VACUUM")

        if tmp_path.exists():
            tmp_path.unlink()
        dest = sqlite3.connect(str(tmp_path))
        mem.backup(dest)
        dest.close()
        mem.close()

        check_conn = sqlite3.connect(str(tmp_path))
        result = check_conn.execute("PRAGMA integrity_check;").fetchone()[0]
        check_conn.close()
        if result != "ok":
            log.warning("checkpoint compact: integrity check failed (%s) on compacted copy, aborting swap", result)
            tmp_path.unlink(missing_ok=True)
            return

        old_size = db_path.stat().st_size
        _os_mod.replace(str(tmp_path), str(db_path))  # atomic on POSIX
        for sidecar in ("-shm", "-wal"):
            p = db_path.with_name(db_path.name + sidecar)
            if p.exists():
                p.unlink()
        new_size = db_path.stat().st_size
        log.info("checkpoint compact: %d -> %d bytes (%.1fs)", old_size, new_size, _time_mod.time() - t0)
    except Exception as exc:
        log.warning("checkpoint compact failed (non-fatal, original file untouched): %s", exc)
        tmp_path.unlink(missing_ok=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from langgraph.checkpoint.sqlite import SqliteSaver
    import langchain_learning.session_graph as sg
    import os as _os_mod

    pid = _os_mod.getpid()
    # task:ac5df3db — kickstart -k restarts were leaving langgraph_checkpoints.db
    # readonly for the next process; these markers exist so a recurrence is
    # traceable to a specific PID/phase (compact vs. checkpointer-open vs.
    # checkpointer-close) instead of just "readonly database" with no context.
    log.info("hook-server[pid=%d]: lifespan startup begin", pid)
    _trim_checkpoints(_CHECKPOINT_DB)
    log.info("hook-server[pid=%d]: pre-compact, about to call _compact_checkpoint_db", pid)
    _compact_checkpoint_db(_CHECKPOINT_DB)  # startup-only — see docstring for why
    log.info("hook-server[pid=%d]: post-compact, opening SqliteSaver connection", pid)
    with SqliteSaver.from_conn_string(str(_CHECKPOINT_DB)) as checkpointer:
        # task:ac5df3db — from_conn_string() opens a bare sqlite3.connect() with no
        # PRAGMA set, so this file has always run in SQLite's default "delete"
        # (rollback-journal) mode: every write creates+deletes a `-journal` sidecar
        # file in ~/.claude/, and any transient failure doing so surfaces as
        # "attempt to write a readonly database" — independent of restart timing,
        # which matches the observed recurrence well after a clean startup. WAL
        # avoids that per-write journal-file dance entirely (single append-only
        # -wal file) and is the standard mode for concurrent-access SQLite.
        mode = checkpointer.conn.execute("PRAGMA journal_mode=WAL;").fetchone()[0]
        log.info("hook-server[pid=%d]: checkpoint db journal_mode=%s", pid, mode)
        sg._graph = sg.build_session_graph(checkpointer=checkpointer)
        import hooks.server_memory as server_memory
        server_memory.load()
        log.info("hook-server[pid=%d]: started, graph built with SqliteSaver, server_session=%s", pid, server_memory.SERVER_SESSION_ID)
        yield
        log.info("hook-server[pid=%d]: shutdown begin, closing SqliteSaver connection", pid)
        sg._graph = None
    log.info("hook-server[pid=%d]: shutdown complete, SqliteSaver connection closed", pid)


from hooks.ui.deps import render as _render, error_partial as _error_partial, JINJA_ENV as _JINJA_ENV
from hooks.ui.routes import ui_router

app = FastAPI(lifespan=lifespan)
app.mount("/ui/static", StaticFiles(directory=str(_HOOKS_DIR / "static")), name="ui-static")
app.include_router(ui_router)


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
    """Delete session checkpoint on SessionEnd.

    NOT called on Stop — Stop fires every assistant turn, so evicting there wipes
    cross-turn checkpoint state (active task, turn counter). Eviction belongs to the
    real session-close signal (SessionEnd). See bug:b7cb4eb4.
    """
    import langchain_learning.session_graph as sg
    if not session_id or not sg._graph:
        return
    try:
        sg._graph.checkpointer.delete_thread(session_id)
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
    _trim_checkpoints(_CHECKPOINT_DB)
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
    SessionEnd. Normally returns {}; returns a one-shot decision:"block" +
    sound-alert reason on the first Stop of a turn (see NoopNode).
    """
    from hooks.dispatcher import _handle_stop
    body = await request.json()
    result = _handle_stop(body)
    return JSONResponse(content=result or {})


@app.post("/hook/SessionStart")
async def session_start(request: Request):
    """SessionStart hook — logs each new or resumed session."""
    body = await request.json()
    from hooks.dispatcher import _handle_session_start
    _handle_session_start(body)
    return JSONResponse(content={})


@app.post("/hook/SessionEnd")
async def session_end(request: Request):
    """SessionEnd hook — the session has actually closed; evict its checkpoint.

    This is the correct place to reclaim MemorySaver storage (fires once when the
    session ends, unlike Stop which fires every turn). Always returns {}.
    """
    body = await request.json()
    from hooks.dispatcher import _handle_session_end
    _handle_session_end(body)
    return JSONResponse(content={})


@app.get("/health")
async def health():
    """Health check — returns status=ok."""
    return {"status": "ok"}


@app.get("/session/active")
async def session_active():
    """Active task — returns the task currently active in the live MemorySaver checkpoint.

    Returns {task_id, title, session_id, turn} if a task is active, or {} if none.
    Source is the in-memory MemorySaver (not the DB) so reflects real-time state.
    """
    from hooks.ui.deps import get_active_session
    return JSONResponse(content=get_active_session())


@app.get("/session/current")
async def session_current():
    """Current session_id — from the single most-recent checkpoint write, no active
    task required. Use this (not /session/active) when no task has been activated
    yet — that's the case /session/active can't answer, since it only returns a
    session_id when active_task_id is set. Returns {} if no checkpoint exists yet.
    """
    from hooks.ui.deps import get_current_session
    return JSONResponse(content=get_current_session())


@app.get("/session/memory")
async def session_memory(n_events: int = 50):
    """Server session memory — last N events from the unified chronological timeline.

    Free-flowing event log: prompts, tool calls, task activations, and assistant
    turns interleaved with timestamps. Durable across reloads (SQLite-backed).
    """
    import hooks.server_memory as server_memory
    return server_memory.get_server_memory(n_events=n_events)






@app.get("/session")
async def session():
    """Session list — returns all sessions with checkpoint counts from SqliteSaver."""
    import langchain_learning.session_graph as sg
    checkpointer = sg._graph.checkpointer if sg._graph else None
    counts: dict[str, int] = {}
    if checkpointer:
        try:
            for tup in checkpointer.list(None):
                sid = tup.config["configurable"]["thread_id"]
                counts[sid] = counts.get(sid, 0) + 1
        except Exception:
            pass
    sessions = [{"session_id": sid, "turns": n} for sid, n in counts.items()]
    return {"count": len(sessions), "sessions": sessions}
