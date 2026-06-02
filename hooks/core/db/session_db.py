"""File-backed SQLite session store — persists across server restarts."""
import json
import sqlite3
from pathlib import Path

_DEFAULT_PATH = Path(__file__).parents[3] / "sessions.db"

_ENSURE = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id     TEXT PRIMARY KEY,
    keywords       TEXT DEFAULT '',
    domains        TEXT DEFAULT '',
    injected_names TEXT DEFAULT '',
    current_state  TEXT DEFAULT 'start',
    state_history  TEXT DEFAULT '[]',
    tasks          TEXT DEFAULT '[]',
    turn           INTEGER DEFAULT 0,
    updated_at     TIMESTAMP DEFAULT (datetime('now'))
)
"""

_ENSURE_SUMMARIES = """
CREATE TABLE IF NOT EXISTS session_summaries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    summary     TEXT NOT NULL,
    tags        TEXT DEFAULT '',
    turn_at     INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT (datetime('now')),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
)
"""

_MIGRATE_SUMMARIES_TAGS = "ALTER TABLE session_summaries ADD COLUMN tags TEXT DEFAULT ''"

_ENSURE_PROMPT_TOOLS = """
CREATE TABLE IF NOT EXISTS prompt_tool_calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    prompt_id   TEXT NOT NULL,
    session_id  TEXT NOT NULL,
    tool_name   TEXT NOT NULL,
    tool_input  TEXT DEFAULT '{}',
    tool_use_id TEXT DEFAULT '',
    called_at   TIMESTAMP DEFAULT (datetime('now'))
)
"""

_MIGRATE_PROMPT_TOOL_INPUT  = "ALTER TABLE prompt_tool_calls ADD COLUMN tool_input TEXT DEFAULT '{}'"
_MIGRATE_PROMPT_TOOL_USE_ID = "ALTER TABLE prompt_tool_calls ADD COLUMN tool_use_id TEXT DEFAULT ''"

_MIGRATE_TASKS      = "ALTER TABLE sessions ADD COLUMN tasks TEXT DEFAULT '[]'"
_MIGRATE_DROP_TOOLS = "ALTER TABLE sessions DROP COLUMN tool_history"

_MAX_SESSIONS = 50


class SessionDB:
    """File-backed session store.

    ``__init__`` is minimal and does no I/O (per the project's OOP rules); it opens
    the connection but defers schema creation/migration. Prefer the ``open()``
    named constructor, which connects *and* runs ``_init_schema()`` so the DB is
    ready to use. Calling ``SessionDB()`` directly also works — schema is created
    lazily on the first operation.
    """

    def __init__(self, path: Path | None = None, max_sessions: int = _MAX_SESSIONS):
        self._max  = max_sessions
        db_path    = path or _DEFAULT_PATH
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._schema_ready = False

    @classmethod
    def open(cls, path: Path | None = None, max_sessions: int = _MAX_SESSIONS) -> "SessionDB":
        """Named constructor: build the store and initialize its schema eagerly."""
        db = cls(path=path, max_sessions=max_sessions)
        db._init_schema()
        return db

    def _init_schema(self) -> None:
        """Create tables + apply migrations once (idempotent)."""
        if self._schema_ready:
            return
        self._conn.execute(_ENSURE)
        self._conn.execute(_ENSURE_SUMMARIES)
        self._conn.execute(_ENSURE_PROMPT_TOOLS)
        for migration in (_MIGRATE_TASKS, _MIGRATE_DROP_TOOLS, _MIGRATE_SUMMARIES_TAGS,
                          _MIGRATE_PROMPT_TOOL_INPUT, _MIGRATE_PROMPT_TOOL_USE_ID):
            try:
                self._conn.execute(migration)
            except Exception:
                pass  # column already exists or not present
        self._conn.commit()
        self._schema_ready = True

    def upsert(self, session_id: str, entry: dict) -> None:
        self._init_schema()
        keywords       = ",".join(sorted(entry.get("keywords", set())))
        domains        = ",".join(sorted(entry.get("domains", set())))
        injected_names = ",".join(sorted(entry.get("injected_names", set())))
        current_state  = entry.get("current_state", "start")
        state_history  = json.dumps(entry.get("state_history", []))
        tasks          = json.dumps(list(entry.get("tasks", [])))
        turn           = entry.get("turn", 0)

        self._conn.execute("""
            INSERT INTO sessions
                (session_id, keywords, domains, injected_names, current_state, state_history, tasks, turn, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(session_id) DO UPDATE SET
                keywords       = excluded.keywords,
                domains        = excluded.domains,
                injected_names = excluded.injected_names,
                current_state  = excluded.current_state,
                state_history  = excluded.state_history,
                tasks          = excluded.tasks,
                turn           = excluded.turn,
                updated_at     = excluded.updated_at
        """, (session_id, keywords, domains, injected_names, current_state, state_history, tasks, turn))
        self._conn.execute("""
            DELETE FROM sessions WHERE session_id NOT IN (
                SELECT session_id FROM sessions
                ORDER BY updated_at DESC, rowid DESC
                LIMIT ?
            )
        """, (self._max,))
        self._conn.commit()

    def get(self, session_id: str) -> dict | None:
        self._init_schema()
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        if not row:
            return None
        return {
            "session_id":     session_id,
            "keywords":       [k for k in row["keywords"].split(",") if k],
            "domains":        [d for d in row["domains"].split(",") if d],
            "injected_names": [n for n in row["injected_names"].split(",") if n],
            "current_state":  row["current_state"],
            "state_history":  json.loads(row["state_history"] or "[]"),
            "tasks":          json.loads(row["tasks"] or "[]"),
            "turn":           row["turn"],
            "updated_at":     row["updated_at"],
        }

    def delete(self, session_id: str) -> bool:
        """Delete a session by ID. Returns True if a row was deleted."""
        self._init_schema()
        cur = self._conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def save_summary(self, session_id: str, summary: str, tags: list[str] | None = None, turn_at: int = 0) -> int:
        self._init_schema()
        tags_str = ",".join(tags) if tags else ""
        cur = self._conn.execute(
            "INSERT INTO session_summaries (session_id, summary, tags, turn_at) VALUES (?, ?, ?, ?)",
            (session_id, summary, tags_str, turn_at),
        )
        self._conn.commit()
        return cur.lastrowid

    def delete_summary(self, summary_id: int) -> bool:
        """Delete a summary by its row ID. Returns True if a row was deleted."""
        self._init_schema()
        cur = self._conn.execute("DELETE FROM session_summaries WHERE id = ?", (summary_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def get_summaries(self, session_id: str) -> list[dict]:
        self._init_schema()
        rows = self._conn.execute(
            "SELECT id, summary, tags, turn_at, created_at FROM session_summaries WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "summary": r["summary"],
                "tags": [t for t in (r["tags"] or "").split(",") if t],
                "turn_at": r["turn_at"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def search_summaries(self, keywords: list[str], top_k: int = 5, session_id: str | None = None) -> list[dict]:
        """Keyword-scored cross-session summary search. Tags weighted 3x, summary body 1x.

        If session_id is given, restricts search to that session only.
        """
        self._init_schema()
        if session_id:
            rows = self._conn.execute(
                "SELECT id, session_id, summary, tags, turn_at, created_at FROM session_summaries WHERE session_id = ?",
                (session_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT id, session_id, summary, tags, turn_at, created_at FROM session_summaries"
            ).fetchall()

        def _score(row) -> int:
            kw_set   = set(keywords)
            tag_hits = sum(3 for t in (row["tags"] or "").split(",") if t.strip() in kw_set)
            body_hits = sum(1 for w in row["summary"].lower().split() if w.strip(".,;:") in kw_set)
            return tag_hits + body_hits

        scored = sorted(rows, key=_score, reverse=True)
        return [
            {
                "id":         r["id"],
                "session_id": r["session_id"],
                "summary":    r["summary"],
                "tags":       [t for t in (r["tags"] or "").split(",") if t],
                "turn_at":    r["turn_at"],
                "created_at": r["created_at"],
                "score":      _score(r),
            }
            for r in scored[:top_k]
            if _score(r) > 0
        ]

    def record_prompt_tool(self, prompt_id: str, session_id: str, tool_name: str, tool_input: dict | None = None, tool_use_id: str = "") -> None:
        """Insert one row binding a tool call to a prompt_id."""
        self._init_schema()
        self._conn.execute(
            "INSERT INTO prompt_tool_calls (prompt_id, session_id, tool_name, tool_input, tool_use_id) VALUES (?, ?, ?, ?, ?)",
            (prompt_id, session_id, tool_name, json.dumps(tool_input or {}), tool_use_id),
        )
        self._conn.commit()

    def prompt_had_tool(self, prompt_id: str, tool_name: str) -> bool:
        """Return True if tool_name was called under prompt_id."""
        if not prompt_id:
            return False
        self._init_schema()
        row = self._conn.execute(
            "SELECT 1 FROM prompt_tool_calls WHERE prompt_id = ? AND tool_name = ? LIMIT 1",
            (prompt_id, tool_name),
        ).fetchone()
        return row is not None

    def get_prompt_tools(self, prompt_id: str) -> list[dict]:
        """Return all tool calls made under a given prompt_id, in call order."""
        self._init_schema()
        rows = self._conn.execute(
            "SELECT tool_name, tool_input, tool_use_id, called_at FROM prompt_tool_calls WHERE prompt_id = ? ORDER BY id",
            (prompt_id,),
        ).fetchall()
        return [{"tool_name": r["tool_name"], "tool_input": json.loads(r["tool_input"] or "{}"),
                 "tool_use_id": r["tool_use_id"], "called_at": r["called_at"]} for r in rows]

    def get_session_tool_counts(self, session_id: str) -> dict[str, int]:
        """Return {tool_name: count} for all tool calls in a session, across all prompts."""
        self._init_schema()
        rows = self._conn.execute(
            "SELECT tool_name, COUNT(*) as cnt FROM prompt_tool_calls WHERE session_id = ? GROUP BY tool_name",
            (session_id,),
        ).fetchall()
        return {r["tool_name"]: r["cnt"] for r in rows}

    def get_session_tools(self, session_id: str) -> list[dict]:
        """Return all tool calls for a session ordered by call time, with prompt_id grouping."""
        self._init_schema()
        rows = self._conn.execute(
            "SELECT prompt_id, tool_name, tool_input, tool_use_id, called_at FROM prompt_tool_calls WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return [{"prompt_id": r["prompt_id"], "tool_name": r["tool_name"],
                 "tool_input": json.loads(r["tool_input"] or "{}"),
                 "tool_use_id": r["tool_use_id"], "called_at": r["called_at"]} for r in rows]

    def all(self) -> list[dict]:
        """Return all sessions ordered by most recently updated."""
        self._init_schema()
        rows = self._conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC, rowid DESC"
        ).fetchall()
        return [self.get(r["session_id"]) for r in rows]
