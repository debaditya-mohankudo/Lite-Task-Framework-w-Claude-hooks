"""Server session memory — durable consolidated context store (SQLite + in-memory session).

Answers "what was I working on?" with one consolidated view: recent prompts, MCP
tool calls, and activated tasks as a real chronological event sequence with
timestamps. Deliberately redundant with task_events / hook logs; the value is
consolidation in one queryable place.

Two layers:
  - SQLite (~/.claude/server_memory.sqlite) — durable backing, capped rolling window.
  - In-memory session cache — fast reads; hydrated from SQLite on server startup
    (ServerMemory.load()), so a reload/restart keeps the context. Writes are
    write-through (cache + DB).

Schema is a single event table: a `type` discriminator ('prompt'|'tool'|'task'),
a shared `content` column (prompt text / tool short-name / task title), and `ref`
(task_id for tasks). One ORDER BY = the interleaved timeline.

SERVER_SESSION_ID tags each row with the writing run; it is not a lifecycle
boundary — rows from many runs coexist.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path

from src.logger import get_logger

_log = get_logger(__name__)

SERVER_SESSION_ID = uuid.uuid4().hex[:12]
STARTED_AT = time.time()

# Skip test sessions so the durable store isn't polluted (past_mistakes.md #5).
_TEST_PREFIXES = ("test-", "pytest-", "api-test-")


class ServerMemory:
    """SQLite-backed consolidated context store with a hydrated in-memory session."""

    _DB = Path.home() / ".claude" / "server_memory.sqlite"
    _MAX_ENTRIES = 1000          # rolling window — newest N events kept
    _cache: list[dict] = []      # in-memory session: chronological event dicts

    # ── storage ──────────────────────────────────────────────────────────────

    @classmethod
    def _connect(cls) -> sqlite3.Connection:
        conn = sqlite3.connect(str(cls._DB), timeout=5)
        # Migrate the (ephemeral, capped) store if it predates the type/content schema.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(server_memory)")}
        if cols and "type" not in cols:
            conn.execute("DROP TABLE server_memory")
            conn.commit()
        conn.execute(
            """CREATE TABLE IF NOT EXISTS server_memory (
                   id                INTEGER PRIMARY KEY,
                   server_session_id TEXT,
                   claude_session_id TEXT,
                   ts                REAL,
                   type              TEXT,   -- 'prompt' | 'tool' | 'task'
                   content           TEXT,   -- prompt text / tool short-name / task title
                   ref               TEXT    -- task_id for tasks; NULL otherwise
               )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sm_ts ON server_memory(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sm_type ON server_memory(type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_sm_session ON server_memory(claude_session_id)")
        return conn

    @classmethod
    def load(cls) -> None:
        """Hydrate the in-memory session from SQLite — call at server startup/reload."""
        try:
            conn = cls._connect()
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    "SELECT claude_session_id, ts, type, content, ref FROM server_memory "
                    "ORDER BY id DESC LIMIT ?",
                    (cls._MAX_ENTRIES,),
                ).fetchall()
            finally:
                conn.close()
            cls._cache = [dict(r) for r in reversed(rows)]
            _log.info("[server_memory] loaded %d events from %s", len(cls._cache), cls._DB)
        except Exception as exc:
            _log.warning("[server_memory] load failed: %s", exc)
            cls._cache = []

    @classmethod
    def _insert(cls, claude_session_id: str, *, type: str, content: str, ref: str | None = None) -> None:
        if (claude_session_id or "").startswith(_TEST_PREFIXES):
            return
        ev = {
            "claude_session_id": claude_session_id or "",
            "ts": time.time(),
            "type": type,
            "content": content,
            "ref": ref,
        }
        # Write-through to SQLite (durable), then mirror into the in-memory session.
        try:
            conn = cls._connect()
            try:
                conn.execute(
                    """INSERT INTO server_memory
                       (server_session_id, claude_session_id, ts, type, content, ref)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (SERVER_SESSION_ID, ev["claude_session_id"], ev["ts"], type, content, ref),
                )
                conn.execute(
                    "DELETE FROM server_memory WHERE id NOT IN "
                    "(SELECT id FROM server_memory ORDER BY id DESC LIMIT ?)",
                    (cls._MAX_ENTRIES,),
                )
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            _log.warning("[server_memory] insert failed: %s", exc)
        cls._cache.append(ev)
        if len(cls._cache) > cls._MAX_ENTRIES:
            del cls._cache[:-cls._MAX_ENTRIES]

    # ── record ───────────────────────────────────────────────────────────────

    @classmethod
    def record_prompt(cls, claude_session_id: str, text: str) -> None:
        if text:
            cls._insert(claude_session_id, type="prompt", content=text)

    @classmethod
    def record_tool(cls, claude_session_id: str, tool: str) -> None:
        if tool:
            cls._insert(claude_session_id, type="tool", content=tool)

    @classmethod
    def record_task(cls, claude_session_id: str, task_id: str, title: str) -> None:
        if task_id:
            cls._insert(claude_session_id, type="task", content=title or "", ref=task_id)

    @classmethod
    def record_turn(cls, claude_session_id: str, summary: str) -> None:
        if summary:
            cls._insert(claude_session_id, type="turn", content=summary)

    # ── read (from the in-memory session) ─────────────────────────────────────

    @classmethod
    def get(cls, n_prompts: int = 20, m_tasks: int = 10, k_tools: int = 30, n_events: int = 50, n_turns: int = 10) -> dict:
        """Per-kind windows + a unified chronological event sequence + totals.

        Served from the in-memory session (hydrated from SQLite at startup).
        `events` is the real interleaved timeline with timestamps.
        """
        cache = cls._cache

        def _window(t: str, lim: int, mapper) -> list[dict]:
            if lim <= 0:
                return []
            return [mapper(e) for e in [e for e in cache if e["type"] == t][-lim:]]

        prompts = _window("prompt", max(0, n_prompts),
                          lambda e: {"claude_session_id": e["claude_session_id"], "ts": e["ts"], "text": e["content"]})
        tasks = _window("task", max(0, m_tasks),
                        lambda e: {"claude_session_id": e["claude_session_id"], "ts": e["ts"], "task_id": e["ref"], "title": e["content"]})
        tools = _window("tool", max(0, k_tools),
                        lambda e: {"claude_session_id": e["claude_session_id"], "ts": e["ts"], "tool": e["content"]})
        turns = _window("turn", max(0, n_turns),
                        lambda e: {"claude_session_id": e["claude_session_id"], "ts": e["ts"], "summary": e["content"]})
        events = [dict(e) for e in (cache[-n_events:] if n_events > 0 else [])]

        return {
            "server_session_id": SERVER_SESSION_ID,
            "started_at": STARTED_AT,
            "n_prompts_total": sum(1 for e in cache if e["type"] == "prompt"),
            "n_tasks_total": sum(1 for e in cache if e["type"] == "task"),
            "n_tools_total": sum(1 for e in cache if e["type"] == "tool"),
            "n_turns_total": sum(1 for e in cache if e["type"] == "turn"),
            "prompts": prompts,
            "tasks": tasks,
            "tools": tools,
            "turns": turns,
            "events": events,
        }

    @classmethod
    def reset(cls) -> None:
        """Clear both layers — test helper / manual clear."""
        cls._cache = []
        try:
            conn = cls._connect()
            try:
                conn.execute("DELETE FROM server_memory")
                conn.commit()
            finally:
                conn.close()
        except Exception as exc:
            _log.warning("[server_memory] reset failed: %s", exc)


# ---------------------------------------------------------------------------
# Module-level API — thin delegators + hook-payload helpers (keep call sites stable)
# ---------------------------------------------------------------------------

def load() -> None:
    ServerMemory.load()


def record_prompt(claude_session_id: str, text: str) -> None:
    ServerMemory.record_prompt(claude_session_id, text)


def record_tool(claude_session_id: str, tool: str) -> None:
    ServerMemory.record_tool(claude_session_id, tool)


def record_task(claude_session_id: str, task_id: str, title: str) -> None:
    ServerMemory.record_task(claude_session_id, task_id, title)


def record_turn(claude_session_id: str, summary: str) -> None:
    ServerMemory.record_turn(claude_session_id, summary)


def get_server_memory(n_prompts: int = 20, m_tasks: int = 10, k_tools: int = 30, n_events: int = 50, n_turns: int = 10) -> dict:
    return ServerMemory.get(n_prompts=n_prompts, m_tasks=m_tasks, k_tools=k_tools, n_events=n_events, n_turns=n_turns)


def reset() -> None:
    ServerMemory.reset()


def _title_from_response(tresp) -> str:
    """Pull title from a PostToolUse tool_response, unwrapping the MCP content envelope.

    Claude Code wraps MCP results as {"content": [{"type": "text", "text": "<json>"}]}.
    Best-effort — the envelope shape varies, so DB lookup is the authoritative source.
    """
    if not isinstance(tresp, dict):
        return ""
    if isinstance(tresp.get("content"), list) and tresp["content"]:
        import json
        try:
            tresp = json.loads(tresp["content"][0].get("text", "") or "{}")
        except Exception:
            return ""
    return tresp.get("title", "") if isinstance(tresp, dict) else ""


def _title_for_task(task_id: str) -> str:
    """Authoritative title lookup from proj_tasks.db (read-only), like activate_task does."""
    if not task_id:
        return ""
    try:
        from langchain_learning.config import config as _cfg
        if not _cfg.tasks_db.exists():
            return ""
        conn = sqlite3.connect(f"file:{_cfg.tasks_db}?mode=ro", uri=True)
        try:
            row = conn.execute("SELECT title FROM open_tasks WHERE id = ?", (task_id,)).fetchone()
        finally:
            conn.close()
        return row[0] if row else ""
    except Exception as exc:
        _log.warning("server_memory: title lookup failed for %s: %s", task_id, exc)
        return ""


def record_tool_from_hook(body: dict) -> None:
    """Record an MCP tool call (short name only) from a raw PostToolUse hook payload."""
    tool_name = body.get("tool_name", "")
    if not tool_name.startswith("mcp__"):
        return
    try:
        from core.tool_registry import strip_mcp_prefix
        short = strip_mcp_prefix(tool_name) or tool_name
    except Exception:
        short = tool_name
    record_tool(body.get("session_id", ""), short)


def record_turn_from_hook(body: dict) -> None:
    """Record the last assistant turn summary from a Stop hook payload.

    Reads transcript_path from the payload, scans backward through the JSONL for
    the last assistant block, and stores the first 200 chars as the turn summary.
    Falls back to "[turn]" if the transcript is missing or unreadable.
    """
    import json as _json
    session_id = body.get("session_id", "")
    summary = "[turn]"
    transcript_path = body.get("transcript_path", "")
    if transcript_path:
        try:
            lines = Path(transcript_path).read_text(encoding="utf-8").splitlines()
            for line in reversed(lines):
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = _json.loads(line)
                except Exception:
                    continue
                if msg.get("role") == "assistant":
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = (block.get("text") or "").strip()
                                if text:
                                    summary = text[:200]
                                    break
                    elif isinstance(content, str) and content.strip():
                        summary = content.strip()[:200]
                    break
        except Exception as exc:
            _log.debug("[server_memory] record_turn_from_hook: transcript read failed: %s", exc)
    record_turn(session_id, summary)


def record_task_from_hook(body: dict) -> None:
    """Record a task activation from a raw PostToolUse hook payload.

    Handles the fully-qualified MCP tool_name (mcp__claude-hooks__tasks__set_active);
    title resolved authoritatively from proj_tasks.db, falling back to the response.
    """
    tool_name = body.get("tool_name", "")
    if not tool_name.startswith("mcp__"):
        return
    try:
        from core.tool_registry import strip_mcp_prefix
        short = strip_mcp_prefix(tool_name) or tool_name
    except Exception:
        short = tool_name
    if short != "tasks__set_active":
        return
    tin = body.get("tool_input") or {}
    task_id = tin.get("task_id", "") if isinstance(tin, dict) else ""
    title = _title_for_task(task_id) or _title_from_response(body.get("tool_response"))
    record_task(body.get("session_id", ""), task_id, title)
