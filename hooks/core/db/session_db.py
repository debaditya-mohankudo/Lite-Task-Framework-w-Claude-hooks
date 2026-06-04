"""File-backed SQLite session store — session_summaries only."""
import sqlite3
from pathlib import Path

_DEFAULT_PATH = Path(__file__).parents[3] / "sessions.db"

_ENSURE_SUMMARIES = """
CREATE TABLE IF NOT EXISTS session_summaries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    summary     TEXT NOT NULL,
    tags        TEXT DEFAULT '',
    turn_at     INTEGER DEFAULT 0,
    created_at  TIMESTAMP DEFAULT (datetime('now'))
)
"""

_MIGRATE_SUMMARIES_TAGS = "ALTER TABLE session_summaries ADD COLUMN tags TEXT DEFAULT ''"

_MAX_SESSIONS = 50


class SessionDB:
    def __init__(self, path: Path | None = None, max_sessions: int = _MAX_SESSIONS):
        self._max  = max_sessions
        db_path    = path or _DEFAULT_PATH
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._schema_ready = False

    @classmethod
    def open(cls, path: Path | None = None, max_sessions: int = _MAX_SESSIONS) -> "SessionDB":
        db = cls(path=path, max_sessions=max_sessions)
        db._init_schema()
        return db

    def _init_schema(self) -> None:
        if self._schema_ready:
            return
        self._conn.execute(_ENSURE_SUMMARIES)
        try:
            self._conn.execute(_MIGRATE_SUMMARIES_TAGS)
        except Exception:
            pass
        self._conn.commit()
        self._schema_ready = True

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
                "id":         r["id"],
                "summary":    r["summary"],
                "tags":       [t for t in (r["tags"] or "").split(",") if t],
                "turn_at":    r["turn_at"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def search_summaries(self, keywords: list[str], top_k: int = 5, session_id: str | None = None) -> list[dict]:
        """Keyword-scored cross-session summary search. Tags weighted 3x, summary body 1x."""
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

        kw_set = set(keywords)

        def _score(row) -> int:
            tag_hits  = sum(3 for t in (row["tags"] or "").split(",") if t.strip() in kw_set)
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

    def list_session_ids(self) -> list[str]:
        self._init_schema()
        rows = self._conn.execute(
            "SELECT DISTINCT session_id FROM session_summaries ORDER BY MAX(created_at) DESC"
        ).fetchall()
        return [r["session_id"] for r in rows]
