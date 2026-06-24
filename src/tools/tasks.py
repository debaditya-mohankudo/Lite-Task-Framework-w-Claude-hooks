"""MCP tools for task management — proj_tasks.db in ~/.claude/.

Migrated from claude_for_mac_local to make claude-hooks self-contained.
Covers full task lifecycle (CRUD, activation, history) and semantic neighbors via TurboVec.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from pathlib import Path
from typing import Optional

import numpy as np

from src.logger import get_logger

_log = get_logger(__name__)
_DB = Path.home() / ".claude" / "proj_tasks.db"

_ICLOUD_DB   = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Databases"
_TASKS_TVIM  = _ICLOUD_DB / "tasks_embeddings.tvim"
_TASKS_META  = _ICLOUD_DB / "tasks_embeddings.meta.json"
_EMBED_MODEL = "nomic-embed-text"
_TOP_K       = 3

_STOPWORDS = {
    "the", "and", "for", "this", "that", "with", "from", "have", "been",
    "will", "are", "was", "but", "not", "can", "also", "all", "its", "our",
    "when", "then", "into", "onto", "over", "make", "need", "use", "get",
    "set", "add", "new", "via", "per", "any", "how", "what", "where", "who",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _tokenise(text: str) -> list[str]:
    tokens = re.findall(r"[a-z]{3,}", text.lower())
    return [t for t in tokens if t not in _STOPWORDS]


def _auto_tags(title: str, body: str) -> str:
    tokens = _tokenise(f"{title} {body}")
    seen: dict[str, int] = {}
    for t in tokens:
        seen[t] = seen.get(t, 0) + 1
    top = sorted(seen, key=lambda k: -seen[k])[:8]
    return ",".join(top)


_ISSUE_TYPES   = {"epic", "story", "task", "bug", "subtask", "review"}
_VALID_STATUSES = {"open", "active", "review", "done", "abandoned", "blocked"}
_TRANSITIONS: dict[str, set[str]] = {
    "open":      {"active"},
    "active":    {"review", "open"},
    "review":    {"done", "active"},
    "done":      set(),
    "abandoned": set(),
    "blocked":   {"review", "active"},
}


def is_valid_transition(from_status: str, to_status: str) -> bool:
    """Return True if transitioning from_status → to_status is allowed.

    Any status can transition to 'abandoned'. Same-status is always allowed.
    """
    if to_status == from_status:
        return True
    if to_status == "abandoned":
        return True
    return to_status in _TRANSITIONS.get(from_status, set())


def _task_row(row: sqlite3.Row) -> dict:
    keys = row.keys()
    return {
        "id":         row["id"],
        "title":      row["title"],
        "body":       row["body"] or "",
        "tags":       row["tags"] or "",
        "status":     row["status"],
        "issue_type": row["issue_type"] if "issue_type" in keys else "task",
        "parent_id":  row["parent_id"] if "parent_id" in keys else None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def _extract_parent_id(tags: str) -> Optional[str]:
    for tag in tags.split(","):
        tag = tag.strip()
        if tag.startswith("parent:"):
            return tag[len("parent:"):]
    return None


def _domain_from_cwd(cwd: str) -> Optional[str]:
    """Match cwd path components against cwd_domain_map from config."""
    try:
        from src.config import config as _src_cfg
        cwd_map = _src_cfg.cwd_domain_map
        for part in reversed(Path(cwd).resolve().parts):
            if part in cwd_map:
                return cwd_map[part]
    except Exception:
        pass
    return None


def _project_name_from_cwd(cwd: str) -> Optional[str]:
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib  # type: ignore
        except ImportError:
            return None
    p = Path(cwd).resolve()
    for parent in [p, *p.parents]:
        candidate = parent / "pyproject.toml"
        if candidate.exists():
            try:
                data = tomllib.loads(candidate.read_text())
                return data.get("project", {}).get("name") or data.get("tool", {}).get("poetry", {}).get("name")
            except Exception:
                return None
    return None


def _ensure_db(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS open_tasks (
            id         TEXT PRIMARY KEY,
            title      TEXT NOT NULL,
            body       TEXT DEFAULT '',
            tags       TEXT DEFAULT '',
            status     TEXT DEFAULT 'open',
            issue_type           TEXT DEFAULT 'task',
            parent_id            TEXT DEFAULT NULL REFERENCES open_tasks(id),
            created_at TIMESTAMP DEFAULT (datetime('now')),
            updated_at TIMESTAMP DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS review_runs (
            id            TEXT PRIMARY KEY,
            task_id       TEXT NOT NULL REFERENCES open_tasks(id) ON DELETE CASCADE,
            template_name TEXT NOT NULL,
            status        TEXT DEFAULT 'open',
            result        TEXT DEFAULT NULL,
            created_at    TIMESTAMP DEFAULT (datetime('now')),
            updated_at    TIMESTAMP DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id    TEXT NOT NULL,
            prompt_id  TEXT DEFAULT '',
            session_id TEXT DEFAULT '',
            turn       INTEGER DEFAULT 0,
            summary    TEXT DEFAULT '',
            tools      TEXT DEFAULT '',
            related    TEXT DEFAULT '',
            logged_at  TIMESTAMP DEFAULT (datetime('now')),
            FOREIGN KEY (task_id) REFERENCES open_tasks(id) ON DELETE CASCADE
        )
    """)
    # Migrate existing DBs that predate the issue_type / parent_id columns
    cols = {row[1] for row in conn.execute("PRAGMA table_info(open_tasks)")}
    if "issue_type" not in cols:
        conn.execute("ALTER TABLE open_tasks ADD COLUMN issue_type TEXT DEFAULT 'task'")
    if "parent_id" not in cols:
        conn.execute("ALTER TABLE open_tasks ADD COLUMN parent_id TEXT DEFAULT NULL REFERENCES open_tasks(id)")
        # Backfill parent_id from existing parent:<id> tags
        rows = conn.execute("SELECT id, tags FROM open_tasks WHERE tags LIKE '%parent:%'").fetchall()
        for row in rows:
            pid = _extract_parent_id(row["tags"] or "")
            if pid:
                conn.execute("UPDATE open_tasks SET parent_id=? WHERE id=?", (pid, row["id"]))
    conn.commit()


def _migrate(conn: sqlite3.Connection) -> None:
    """Apply additive schema migrations for existing DBs."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(task_events)")}
    if "related" not in cols:
        conn.execute("ALTER TABLE task_events ADD COLUMN related TEXT DEFAULT ''")
        conn.commit()
    if "memories" not in cols:
        conn.execute("ALTER TABLE task_events ADD COLUMN memories TEXT DEFAULT ''")
        conn.commit()
    # wip → open migration (wip removed 2026-06-14); active/review added 2026-06-23
    conn.execute("UPDATE open_tasks SET status='open' WHERE status='wip'")
    conn.commit()
    # Migrate review children from open_tasks → review_runs (2026-06-23)
    # open_tasks rows with issue_type='review' become review_runs rows
    task_cols = {r[1] for r in conn.execute("PRAGMA table_info(open_tasks)")}
    if "review_template_name" in task_cols:
        old_rows = conn.execute(
            "SELECT id, parent_id, review_template_name, status, review_result, created_at, updated_at "
            "FROM open_tasks WHERE issue_type='review'"
        ).fetchall()
        for r in old_rows:
            conn.execute(
                "INSERT OR IGNORE INTO review_runs (id, task_id, template_name, status, result, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (r["id"], r["parent_id"], r["review_template_name"] or "", r["status"],
                 r["review_result"], r["created_at"], r["updated_at"]),
            )
        conn.execute("DELETE FROM open_tasks WHERE issue_type='review'")
        conn.commit()


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB), timeout=5)
    conn.row_factory = sqlite3.Row
    _ensure_db(conn)
    _migrate(conn)
    return conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------



# ---------------------------------------------------------------------------
# Task CRUD
# ---------------------------------------------------------------------------

def handle_create(title: str, body: str = "", cwd: str = "", domain: str = "", parent_id: str = "", session_id: str = "", issue_type: str = "task") -> dict:
    """Create a new open task with auto-generated tags. Returns the task id.

    BEFORE CALLING: the body must start with `Type: <type>` and contain the
    required sections for that type, or the create gate rejects it. Check the
    quick-reference templates in `task_templates/` (one .md per issue_type:
    feature, bug, research, misc, epic) and copy the matching scaffold.

    For subtasks: create a parent task first, then pass parent_id=<parent_task_id> for
    each subtask — tags them as parent:<id>, groups them in tasks__list, and auto-closes
    the parent when all subtasks are done.

    Args:
        title:      Short task title.
        body:       Optional description / context.
        cwd:        Optional working directory — used to detect the project name from
                    pyproject.toml and add a project:<name> tag automatically.
        domain:     Explicit domain tag (e.g. "market-intel", "vault"). Overrides
                    domain inferred from cwd. Use when there is no dev cwd.
        parent_id:  Optional parent task id — appends parent:<id> to tags, making this
                    a subtask. Parent is auto-closed when all its subtasks are done.
        session_id: Current Claude session id — used to append task to all_open_tasks
                    in the LangGraph checkpoint so Claude sees it in Turn state.
        issue_type: Jira-style issue type. One of: epic, story, task, bug, subtask. Default: task.
    """
    if issue_type not in _ISSUE_TYPES:
        return {"error": f"Invalid issue_type '{issue_type}'. Valid: {sorted(_ISSUE_TYPES)}"}
    task_id = uuid.uuid4().hex[:8]
    tags = _auto_tags(title, body)
    resolved_domain = domain.strip() if domain else (_domain_from_cwd(cwd) if cwd else None)
    if resolved_domain:
        tags = f"domain:{resolved_domain},{tags}" if tags else f"domain:{resolved_domain}"
    if cwd:
        project = _project_name_from_cwd(cwd)
        if project:
            tag = f"project:{project}"
            tags = f"{tag},{tags}" if tags else tag
    if parent_id:
        parent_tag = f"parent:{parent_id}"
        tags = f"{parent_tag},{tags}" if tags else parent_tag
    with _connect() as conn:
        if parent_id:
            row = conn.execute("SELECT status FROM open_tasks WHERE id=?", (parent_id,)).fetchone()
            if row is None:
                return {"error": f"Parent task '{parent_id}' not found"}
        conn.execute(
            "INSERT INTO open_tasks (id, title, body, tags, issue_type, parent_id) VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, title.strip(), body.strip(), tags, issue_type, parent_id or None),
        )
    try:
        handle_index_task(task_id)
    except Exception:
        pass
    _log.info("[tasks__create] id=%s issue_type=%s parent=%s title=%r", task_id, issue_type, parent_id or "-", title[:60])
    return {"id": task_id, "title": title, "tags": tags, "status": "open", "issue_type": issue_type}


def handle_create_epic(title: str, motivation: str, files: str = "", cwd: str = "", session_id: str = "") -> dict:
    """Create an epic without the body-template gauntlet.

    Builds the required Type/Task/Motivation/Resolution/Files body internally.

    Args:
        title:      Short epic title.
        motivation: Why this epic is needed.
        files:      Key files involved (optional, comma-separated).
        cwd:        Optional working directory for project tag detection.
        session_id: Current Claude session id.
    """
    body = (
        f"Type: feature\n\n"
        f"Task:\n{title.strip()}\n\n"
        f"Motivation:\n{motivation.strip()}\n\n"
        f"Resolution:\nTBD\n\n"
        f"Files:\n{files.strip() if files else 'TBD'}"
    )
    return handle_create(title=title, body=body, cwd=cwd, session_id=session_id, issue_type="epic")


def handle_list(status: str = "open,active,review,blocked", limit: int = 50) -> list:
    """List tasks filtered by status (comma-separated). Default: open,blocked.

    Tasks are returned in DFS tree order (parent → children → grandchildren).
    Each task includes a 'depth' field (0 = root, 1 = child, 2 = grandchild, …).
    Tasks whose parent is not in the result set are fetched from DB and shown at
    their natural depth; orphans (parent truly missing) appear at depth 0.

    Args:
        status: Comma-separated statuses to include. Values: open, active, review, blocked, done, abandoned.
                A 'blocked' review task (failure found) stays visible by default.
        limit: Max number of tasks to return (default 50).
    """
    statuses = [s.strip() for s in status.split(",") if s.strip()]
    placeholders = ",".join("?" * len(statuses))



    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM open_tasks WHERE status IN ({placeholders}) ORDER BY updated_at DESC LIMIT ?",
            [*statuses, limit],
        ).fetchall()
        task_map: dict[str, dict] = {r["id"]: _task_row(r) for r in rows}

        # Fetch any missing parents (parent filtered out by status) from DB
        missing: set[str] = set()
        for t in task_map.values():
            pid = t.get("parent_id")
            if pid and pid not in task_map:
                missing.add(pid)
        while missing:
            next_missing: set[str] = set()
            for pid in missing:
                row = conn.execute("SELECT * FROM open_tasks WHERE id=?", (pid,)).fetchone()
                if row:
                    t = _task_row(row)
                    t["_context_only"] = True  # parent shown for context, not matching status filter
                    task_map[pid] = t
                    grandparent = t.get("parent_id")
                    if grandparent and grandparent not in task_map:
                        next_missing.add(grandparent)
            missing = next_missing

    # Build adjacency: parent_id → [children]
    children_of: dict[str, list[str]] = {}
    for tid, t in task_map.items():
        pid = t.get("parent_id")
        if pid:
            children_of.setdefault(pid, []).append(tid)

    # Roots: tasks with no parent_id, or whose parent is not in task_map
    roots = [
        tid for tid, t in task_map.items()
        if not t.get("parent_id") or t["parent_id"] not in task_map
    ]
    # Stable order: context-only parents last, then by updated_at desc
    roots.sort(key=lambda tid: (task_map[tid].get("_context_only", False), task_map[tid]["updated_at"]), reverse=True)

    result: list[dict] = []

    def _dfs(tid: str, depth: int, visited: set[str]) -> None:
        if tid in visited:
            return
        visited.add(tid)
        t = task_map[tid].copy()
        t["depth"] = depth
        result.append(t)
        for child_id in children_of.get(tid, []):
            _dfs(child_id, depth + 1, visited)

    visited: set[str] = set()
    for root_id in roots:
        _dfs(root_id, 0, visited)

    # Emit any nodes unreachable from roots (cycle participants) at depth 0
    for tid in task_map:
        if tid not in visited:
            _dfs(tid, 0, visited)

    return result


def handle_get(id: str) -> dict:
    """Return a single task by id.

    Args:
        id: Task id.
    """
    with _connect() as conn:
        row = conn.execute("SELECT * FROM open_tasks WHERE id = ?", (id,)).fetchone()
        if row is None:
            return {"error": f"Task '{id}' not found"}
        return _task_row(row)


def handle_update(id: str, title: str = "", body: str = "", status: str = "", issue_type: str = "", tags: str = "") -> dict:
    """Update task fields. Only provided fields are changed.

    Args:
        id:         Task id.
        title:      New title (optional).
        body:       New or appended body text (optional).
        status:     New status: open, active, review, done, abandoned (optional). Transitions enforced.
        issue_type: New issue type: epic, story, task, bug, subtask (optional).
        tags:       Comma-separated tags to append to existing tags (optional).
    """
    if issue_type and issue_type not in _ISSUE_TYPES:
        return {"error": f"Invalid issue_type '{issue_type}'. Valid: {sorted(_ISSUE_TYPES)}"}
    if status and status not in _VALID_STATUSES:
        return {"error": f"Invalid status '{status}'. Valid: {sorted(_VALID_STATUSES)}"}
    with _connect() as conn:
        row = conn.execute("SELECT * FROM open_tasks WHERE id = ?", (id,)).fetchone()
        if row is None:
            return {"error": f"Task '{id}' not found"}
        new_title      = title.strip()      if title      else row["title"]
        new_body       = body.strip()       if body       else row["body"] or ""
        new_status     = status.strip()     if status     else row["status"]
        if status:
            if not is_valid_transition(row["status"], new_status):
                allowed = sorted(_TRANSITIONS.get(row["status"], set()) | {"abandoned"})
                return {"error": f"Invalid transition '{row['status']}' → '{new_status}'. Allowed: {allowed}"}
        new_issue_type = issue_type.strip() if issue_type else (row["issue_type"] if "issue_type" in row.keys() else "task")
        existing_tags  = row["tags"] or ""
        if tags:
            new_tags_set = set(t.strip() for t in existing_tags.split(",") if t.strip())
            new_tags_set.update(t.strip() for t in tags.split(",") if t.strip())
            new_tags = ",".join(sorted(new_tags_set))
        else:
            new_tags = existing_tags
        conn.execute(
            """UPDATE open_tasks SET title=?, body=?, status=?, issue_type=?, tags=?, updated_at=datetime('now') WHERE id=?""",
            (new_title, new_body, new_status, new_issue_type, new_tags, id),
        )
        # If tags now contain a review:<template> tag, ensure a review run exists.
        # Fail-open: tag write succeeds even if run creation fails.
        review_template = next(
            (t.split(":", 1)[1] for t in new_tags.split(",") if t.startswith("review:") and ":" in t),
            None,
        )
        review_run_id = None
        if review_template:
            try:
                review_run_id = _create_review_run(conn, id, review_template)
            except Exception as exc:
                _log.error("[tasks__update] review run creation failed — continuing: %s", exc)
    _log.info("[tasks__update] id=%s status=%s issue_type=%s", id, new_status, new_issue_type)
    result: dict = {"ok": True, "id": id, "status": new_status, "issue_type": new_issue_type, "tags": new_tags}
    if review_run_id:
        result["review_run_id"] = review_run_id
    return result


def handle_pause(task_id: str, pending: list[str], session_id: str = "") -> dict:
    """Save pending work items to the task body under ## Pending before paused.

    Overwrites any existing ## Pending before paused section (most-recent state only).
    The section is injected into additionalSystemPrompt every turn via load_task_context
    so Claude sees it automatically on resume — no checkpoint changes needed.

    Args:
        task_id:    Active task id.
        pending:    List of pending work items to save.
        session_id: Current Claude session id (unused currently; reserved for future use).
    """
    if not pending:
        return {"error": "pending list must not be empty"}
    items_md = "\n".join(f"- {item}" for item in pending)
    pause_section = f"## Pending before paused\n{items_md}\n---"
    with _connect() as conn:
        row = conn.execute("SELECT body FROM open_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return {"error": f"Task '{task_id}' not found"}
        body: str = row["body"] or ""
        # Replace existing section if present, otherwise append
        marker = "## Pending before paused"
        if marker in body:
            pre = body[: body.index(marker)].rstrip()
            post_raw = body[body.index(marker) :]
            # strip old section (up to next ## or end)
            lines = post_raw.splitlines()
            end = next((i for i, l in enumerate(lines[1:], 1) if l.startswith("## ")), len(lines))
            post = "\n".join(lines[end:]).lstrip()
            new_body = f"{pre}\n\n{pause_section}" + (f"\n\n{post}" if post else "")
        else:
            new_body = (body.rstrip() + "\n\n" + pause_section) if body.strip() else pause_section
        conn.execute(
            "UPDATE open_tasks SET body=?, updated_at=datetime('now') WHERE id=?",
            (new_body, task_id),
        )
    return {"ok": True, "task_id": task_id, "pending_count": len(pending)}


def handle_delete(id: str, session_id: str = "") -> dict:
    """Soft-delete a task by setting status='abandoned'.

    Args:
        id:         Task id.
        session_id: Current Claude session id — used to remove task from all_open_tasks
                    in the LangGraph checkpoint.
    """
    with _connect() as conn:
        conn.execute(
            "UPDATE open_tasks SET status='abandoned', updated_at=datetime('now') WHERE id=?", (id,)
        )
    _log.info("[tasks__delete] id=%s → abandoned", id)
    return {"ok": True, "id": id, "status": "abandoned"}


def handle_search(query: str, status: str = "open,done") -> list:
    """Full-text keyword search over task titles, bodies, and tags.

    Args:
        query:  Space-separated keywords.
        status: Comma-separated statuses to search within. Default: open,done.
    """
    tokens = set(_tokenise(query))
    if not tokens:
        return []
    statuses = [s.strip() for s in status.split(",") if s.strip()]
    placeholders = ",".join("?" * len(statuses))
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM open_tasks WHERE status IN ({placeholders})", statuses,
        ).fetchall()
    scored: list[tuple[int, dict]] = []
    for row in rows:
        haystack = f"{row['title']} {row['body']} {row['tags']}".lower()
        hits = sum(1 for t in tokens if t in haystack)
        if hits > 0:
            scored.append((hits, _task_row(row)))
    scored.sort(key=lambda x: -x[0])
    return [t for _, t in scored]


# ---------------------------------------------------------------------------
# Task activation (checkpoint writes handled by PostToolUse activate_task node)
# ---------------------------------------------------------------------------

def _create_review_run(conn: sqlite3.Connection, task_id: str, template_name: str) -> str:
    """Create a review_runs row for task_id + template if one doesn't already exist. Returns run id."""
    existing = conn.execute(
        "SELECT id FROM review_runs WHERE task_id=? AND template_name=? AND status='open' LIMIT 1",
        (task_id, template_name),
    ).fetchone()
    if existing:
        _log.info("[tasks] review run already exists id=%s — skipping", existing["id"])
        return existing["id"]
    run_id = uuid.uuid4().hex[:8]
    try:
        conn.execute(
            "INSERT INTO review_runs (id, task_id, template_name) VALUES (?, ?, ?)",
            (run_id, task_id, template_name),
        )
        conn.commit()
    except Exception as exc:
        _log.error("[tasks] failed to create review run task=%s template=%s: %s", task_id, template_name, exc)
        raise
    _log.info("[tasks] created review run id=%s template=%s", run_id, template_name)
    return run_id


def handle_set_active(task_id: str, session_id: str) -> dict:
    """Signal that task_id should become the active task for this session.

    Does NOT write status to proj_tasks.db — active task is tracked in the
    LangGraph checkpoint only (via ActivateTaskNode PostToolUse hook).

    Args:
        task_id:    Task id to activate.
        session_id: Current Claude session_id (from Turn state in system prompt).
    """
    with _connect() as conn:
        row = conn.execute("SELECT id, title, tags FROM open_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return {"error": f"task '{task_id}' not found"}

        # Look up existing review run (created by handle_update when review tag was set).
        review_task_id = None
        run_row = conn.execute(
            "SELECT id FROM review_runs WHERE task_id=? AND status IN ('open', 'blocked') LIMIT 1",
            (task_id,),
        ).fetchone()
        if run_row:
            review_task_id = run_row["id"]

    try:
        handle_index_task(task_id)
    except Exception:
        pass
    _log.info("[tasks__set_active] task=%s session=%s title=%r", task_id, session_id[:8] if session_id else "?", row["title"][:60])
    result: dict = {"ok": True, "task_id": task_id, "title": row["title"], "status": "open"}
    if review_task_id:
        result["review_task_id"] = review_task_id
    return result


def handle_clear_active(session_id: str) -> dict:
    """Signal that the active task should be cleared.

    Checkpoint update (zeroing active_task_id) is handled by
    DeactivateTaskNode via the PostToolUse hook — not here.

    Args:
        session_id: Current Claude session_id.
    """
    return {"ok": True, "session_id": session_id}


def handle_pop_active(session_id: str) -> dict:
    """Signal that the task stack should be popped and the previous task re-activated.

    Checkpoint update (popping task_stack, re-activating task) is handled by
    ActivateTaskNode via the PostToolUse hook — not here.

    Args:
        session_id: Current Claude session_id.
    """
    return {"ok": True, "session_id": session_id}


# ---------------------------------------------------------------------------
# Task events and lifecycle
# ---------------------------------------------------------------------------

def handle_log_event(
    task_id: str,
    summary: str,
    tools: str = "",
    prompt_id: str = "",
    session_id: str = "",
    turn: int = 0,
) -> dict:
    """Append a development event to a task's history. Called by the stop hook.

    Args:
        task_id:    Task id to log against.
        summary:    Short description of what happened this turn.
        tools:      Comma-separated tool names called this turn.
        prompt_id:  Prompt UUID from session state.
        session_id: Session id.
        turn:       Turn number within the session.
    """
    with _connect() as conn:
        row = conn.execute("SELECT id FROM open_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return {"error": f"Task '{task_id}' not found"}
        conn.execute(
            """INSERT INTO task_events (task_id, prompt_id, session_id, turn, summary, tools)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (task_id, prompt_id, session_id, turn, summary[:300], tools),
        )
        conn.execute("UPDATE open_tasks SET updated_at=datetime('now') WHERE id=?", (task_id,))
    return {"logged": task_id, "turn": turn}


def handle_finish(task_id: str, session_id: str, reason: str = "") -> dict:
    """Explicitly mark a task as done.

    Marks status='done', logs a final event, auto-closes parent if all subtasks done.
    Checkpoint cleanup (zeroing active_task_id) is handled by DeactivateTaskNode
    via the PostToolUse hook — not here.

    Args:
        task_id:    Task id to finish.
        session_id: Current Claude session_id (from Turn state in system prompt).
        reason:     Optional one-line summary of what was accomplished.
    """
    if not _DB.exists():
        return {"error": "proj_tasks.db not found"}

    # Gate: reject if resolution is still unfilled
    try:
        with _connect() as conn:
            row = conn.execute("SELECT body FROM open_tasks WHERE id=?", (task_id,)).fetchone()
            if row is None:
                return {"error": f"task '{task_id}' not found"}
            body = row["body"] or ""
            import re as _re
            # Matches both "Resolution:\nTBD" and "## Resolution\nTBD" styles.
            # Stops at a blank line, next section heading, or end of string.
            res_match = _re.search(r"(?i)(?:##[ \t]*)?resolution[: \t]*\n?(.*?)(?=\n\n|\n(?:##|\w[\w ]*:)|\Z)", body, _re.DOTALL)
            if res_match:
                res_text = res_match.group(1).strip()
                _UNFILLED = {"tbd", "<to be filled on completion>", "pending", "n/a", ""}
                if res_text.lower() in _UNFILLED:
                    return {
                        "error": (
                            "Cannot finish task — Resolution is still unfilled "
                            f"({res_text!r}). Update the task body with what was actually done."
                        )
                    }
    except Exception as e:
        return {"error": str(e)}

    try:
        with _connect() as conn:
            cur = conn.execute(
                "UPDATE open_tasks SET status='done', updated_at=datetime('now') WHERE id=?",
                (task_id,),
            )
            if cur.rowcount == 0:
                return {"error": f"task '{task_id}' not found"}
            if reason:
                conn.execute(
                    """INSERT INTO task_events (task_id, session_id, summary, tools)
                       VALUES (?, ?, ?, 'tasks__finish')""",
                    (task_id, session_id, reason[:200]),
                )
    except Exception as e:
        return {"error": str(e)}

    parent_closed = None
    try:
        with _connect() as conn:
            row = conn.execute("SELECT parent_id FROM open_tasks WHERE id=?", (task_id,)).fetchone()
            if row:
                pid = row["parent_id"]
                if pid:
                    siblings = conn.execute(
                        "SELECT status FROM open_tasks WHERE parent_id=?", (pid,),
                    ).fetchall()
                    if siblings and all(s["status"] == "done" for s in siblings):
                        conn.execute(
                            "UPDATE open_tasks SET status='done', updated_at=datetime('now') WHERE id=?", (pid,),
                        )
                        conn.execute(
                            """INSERT INTO task_events (task_id, session_id, summary, tools)
                               VALUES (?, ?, 'All subtasks done — auto-closed', 'tasks__finish')""",
                            (pid, session_id),
                        )
                        parent_closed = pid
    except Exception:
        pass

    _log.info("[tasks__finish] task=%s session=%s parent_closed=%s reason=%r",
              task_id, session_id[:8] if session_id else "?", parent_closed or "-", (reason or "")[:60])
    out: dict = {"ok": True, "task_id": task_id, "status": "done"}
    if parent_closed:
        out["parent_closed"] = parent_closed
    try:
        handle_index_task(task_id)
    except Exception:
        pass
    return out


def handle_add_decision(task_id: str, decision: str, session_id: str = "") -> dict:
    """Log an explicit design decision for the active task.

    Persists to task_events and appends to mid_task_decisions in the LangGraph
    checkpoint so it is injected every subsequent turn.

    Args:
        task_id:    Active task id.
        decision:   One-line description of the decision and its rationale.
        session_id: Current Claude session id.
    """
    if not decision.strip():
        return {"error": "decision text is required"}
    with _connect() as conn:
        row = conn.execute("SELECT id FROM open_tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return {"error": f"Task '{task_id}' not found"}
        conn.execute(
            """INSERT INTO task_events (task_id, session_id, summary, tools)
               VALUES (?, ?, ?, 'decision')""",
            (task_id, session_id, decision.strip()[:300]),
        )
    _log.info("[tasks__add_decision] task=%s decision=%r", task_id, decision.strip()[:80])
    return {"logged": task_id, "decision": decision.strip()}


def handle_history(id: str) -> list:
    """Return all logged events for a task in chronological order.

    Args:
        id: Task id.
    """
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id, task_id, prompt_id, session_id, turn, summary, tools, related, memories, logged_at
               FROM task_events WHERE task_id = ? ORDER BY logged_at ASC""",
            (id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Semantic neighbors via TurboVec
# ---------------------------------------------------------------------------

def _task_uid(task_id: str) -> int:
    digest = hashlib.sha256(f"task::{task_id}".encode()).digest()
    return int.from_bytes(digest[:8], "little") & 0x7FFF_FFFF_FFFF_FFFF


def _get_embed_model():
    from llama_index.embeddings.ollama import OllamaEmbedding
    return OllamaEmbedding(model_name=_EMBED_MODEL)


def _extract_project(tags: str) -> str:
    """Extract 'project:foo' value from comma-separated tags, or '' if absent."""
    for tag in (tags or "").split(","):
        tag = tag.strip()
        if tag.startswith("project:"):
            return tag[len("project:"):]
    return ""


def _load_all_tasks() -> list[dict]:
    if not _DB.exists():
        return []
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute(
            "SELECT id, title, body, status, tags FROM open_tasks"
        ).fetchall()]


def rebuild_task_index() -> dict:
    """Full rebuild of the TurboVec semantic index over all tasks."""
    import turbovec
    from tools.rag_core import save_index

    tasks = _load_all_tasks()
    if not tasks:
        return {"ok": False, "error": "no tasks found"}

    model = _get_embed_model()
    texts = [f"{t['title']}\n{t['body'] or ''}" for t in tasks]
    vecs  = np.array([model.get_text_embedding(t) for t in texts], dtype=np.float32)

    index = turbovec.IdMapIndex(vecs.shape[1])
    meta: dict[str, dict] = {}
    for task, vec in zip(tasks, vecs):
        uid = _task_uid(task["id"])
        index.add_with_ids(vec.reshape(1, -1), np.array([uid], dtype=np.uint64))
        meta[str(uid)] = {
            "task_id": task["id"],
            "title":   task["title"],
            "status":  task["status"],
            "project": _extract_project(task.get("tags", "")),
        }
    index.prepare()
    save_index(index, meta, _TASKS_TVIM, _TASKS_META)
    return {"ok": True, "indexed": len(tasks)}


def handle_index_task(task_id: str) -> dict:
    """Incrementally upsert a single task into the TurboVec index.

    Loads the existing index, embeds only this task, upserts by stable UID,
    and saves. Falls back to a full rebuild if the index doesn't exist yet.

    Args:
        task_id: Task id to upsert into the index.
    """
    import turbovec
    from tools.rag_core import load_index, save_index

    if not _DB.exists():
        return {"ok": False, "error": "proj_tasks.db not found"}

    # Full rebuild if no index yet
    if not _TASKS_TVIM.exists():
        return rebuild_task_index()

    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, title, body, status, tags FROM open_tasks WHERE id=?", (task_id,)
        ).fetchone()
    if not row:
        return {"ok": False, "error": f"task not found: {task_id}"}

    task = dict(row)
    index, meta = load_index(_TASKS_TVIM, _TASKS_META)
    if index is None:
        return rebuild_task_index()

    model = _get_embed_model()
    vec = np.array([model.get_text_embedding(f"{task['title']}\n{task['body'] or ''}")], dtype=np.float32)
    uid = _task_uid(task_id)

    index.add_with_ids(vec, np.array([uid], dtype=np.uint64))
    meta[str(uid)] = {
        "task_id": task["id"],
        "title":   task["title"],
        "status":  task["status"],
        "project": _extract_project(task.get("tags", "")),
    }
    index.prepare()
    save_index(index, meta, _TASKS_TVIM, _TASKS_META)
    return {"ok": True, "upserted": task_id}


def handle_neighbors(task_id: str) -> list:
    """Return top-5 semantically similar tasks using TurboVec vector search.

    Rebuilds the index on first call if it doesn't exist yet.
    Returns list of dicts: {task_id, title, status, score}.

    Args:
        task_id: Seed task id to find neighbours for.
    """
    import turbovec
    from tools.rag_core import load_index, query_index

    if not _DB.exists():
        return []

    # Build index if missing
    if not _TASKS_TVIM.exists():
        result = rebuild_task_index()
        if not result["ok"]:
            return []

    index, meta = load_index(_TASKS_TVIM, _TASKS_META)
    if index is None:
        return []

    # Get the seed task's text and project tag
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT title, body, tags FROM open_tasks WHERE id=?", (task_id,)
        ).fetchone()
    if not row:
        return []

    seed_project = _extract_project(row["tags"] or "")
    model = _get_embed_model()
    q_vec = np.array([model.get_text_embedding(f"{row['title']}\n{row['body'] or ''}")], dtype=np.float32)

    # Fetch more candidates when filtering by project to still return TOP_K
    results = query_index(index, meta, q_vec, k=min(len(meta), _TOP_K * 4))
    filtered = [
        {"task_id": r["task_id"], "title": r["title"], "status": r["status"], "score": round(r["score"], 3)}
        for r in results
        if r["task_id"] != task_id
        and (not seed_project or r.get("project") == seed_project)
    ]
    return filtered[:_TOP_K]


# ---------------------------------------------------------------------------
# Review template tools
# ---------------------------------------------------------------------------

_REVIEW_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "review_templates"


def handle_create_review_template(name: str, domain: str, context_prompt: str, checklist: list[str]) -> dict:
    """Write a review template MD file to review_templates/<name>.md.

    Checklist items should be prefixed with [auto] or [manual] and a short id:
      "[auto] c1: label" or "[manual] m1: label"

    Args:
        name:           Template filename (no extension). Used as review:<name> tag.
        domain:         Domain this template applies to (e.g. claude-hooks, vault).
        context_prompt: Instructions for BareClaudeAgent when evaluating auto items.
        checklist:      List of checklist item strings.
    """
    _REVIEW_TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
    path = _REVIEW_TEMPLATES_DIR / f"{name}.md"

    auto_items = [f"- {c}" for c in checklist if c.strip().startswith("[auto]")]
    manual_items = [f"- {c}" for c in checklist if c.strip().startswith("[manual]")]

    lines = [
        "---",
        f"name: {name}",
        f"domain: {domain}",
        f"context_prompt: >",
        *[f"  {line}" for line in context_prompt.splitlines()],
        "---",
        "",
        "## Auto items",
        "",
        *auto_items,
    ]
    if manual_items:
        lines += ["", "## Manual items", "", *manual_items]
    lines.append("")

    try:
        path.write_text("\n".join(lines), encoding="utf-8")
    except Exception as exc:
        _log.error("[create_review_template] failed to write template name=%s path=%s: %s", name, path, exc)
        return {"error": f"failed to write template: {exc}"}
    _log.info("[create_review_template] wrote template name=%s path=%s items=%d", name, path, len(checklist))
    return {"ok": True, "name": name, "path": str(path), "item_count": len(checklist)}


def handle_list_review_templates() -> list[dict]:
    """Scan review_templates/ folder and return list of {name, domain, item_count}.

    Reads frontmatter (name, domain) and counts checklist items from each MD file.
    Always reads fresh from disk — no caching.
    """
    if not _REVIEW_TEMPLATES_DIR.exists():
        return []

    templates = []
    for md_file in sorted(_REVIEW_TEMPLATES_DIR.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
            item_count = content.count("- [auto]") + content.count("- [manual]")
            # Parse frontmatter once — key: value pairs + context_prompt block scalar
            fm: dict[str, str] = {}
            description = ""
            if content.startswith("---"):
                end = content.index("---", 3)
                in_prompt = False
                for line in content[3:end].splitlines():
                    if in_prompt:
                        stripped = line.strip()
                        if stripped:
                            description = stripped
                            in_prompt = False
                    elif line.startswith("context_prompt:"):
                        in_prompt = True
                    elif ":" in line:
                        k, _, v = line.partition(":")
                        fm[k.strip()] = v.strip()
            templates.append({
                "name": fm.get("name", md_file.stem),
                "domain": fm.get("domain", ""),
                "description": description,
                "item_count": item_count,
                "path": str(md_file),
            })
        except Exception as exc:
            _log.warning("[list_review_templates] skipped %s: %s", md_file.name, exc)

    _log.info("[list_review_templates] found=%d", len(templates))
    return templates


# ---------------------------------------------------------------------------
# Review execution tools
# ---------------------------------------------------------------------------


def handle_execute_review(review_task_id: str, results: list[dict]) -> dict:
    """Store Claude's evaluation of auto checklist items for a review run.

    Each result dict must have: id (str), passed (bool), note (str).
    Example: [{"id": "c1", "passed": True, "note": "state keys owned per node"}]

    Manual items are unaffected — use tasks__submit_review_item for those.

    Args:
        review_task_id: Id of the review_runs row.
        results:        List of {id, passed, note} dicts for auto checklist items.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, result FROM review_runs WHERE id=?", (review_task_id,)
        ).fetchone()
        if row is None:
            return {"error": f"review run '{review_task_id}' not found"}

        existing: list[dict] = json.loads(row["result"]) if row["result"] else []
        existing_by_id = {r["id"]: r for r in existing}
        for r in results:
            existing_by_id[r["id"]] = {"id": r["id"], "passed": r.get("passed"), "note": r.get("note", "")}
        merged = list(existing_by_id.values())

        passed = sum(1 for r in merged if r.get("passed") is True)
        failed = sum(1 for r in merged if r.get("passed") is False)
        pending = sum(1 for r in merged if r.get("passed") is None)
        new_status = "blocked" if failed > 0 else ("open" if pending > 0 else "done")

        conn.execute(
            "UPDATE review_runs SET result=?, status=?, updated_at=datetime('now') WHERE id=?",
            (json.dumps(merged), new_status, review_task_id),
        )
        conn.commit()

    _log.info("[execute_review] run=%s passed=%d failed=%d pending=%d status=%s",
              review_task_id, passed, failed, pending, new_status)
    return {"ok": True, "review_task_id": review_task_id, "passed": passed, "failed": failed, "pending": pending, "status": new_status}


def handle_submit_review_item(review_task_id: str, checklist_id: str, passed: bool, note: str = "") -> dict:
    """Human sign-off for a manual checklist item in a review run.

    Args:
        review_task_id: Id of the review_runs row.
        checklist_id:   The item id to sign off (e.g. 'm1').
        passed:         True = approved, False = rejected.
        note:           Optional one-line reasoning.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, result FROM review_runs WHERE id=?", (review_task_id,)
        ).fetchone()
        if row is None:
            return {"error": f"review run '{review_task_id}' not found"}

        existing: list[dict] = json.loads(row["result"]) if row["result"] else []
        by_id = {r["id"]: r for r in existing}
        if checklist_id not in by_id:
            by_id[checklist_id] = {"id": checklist_id}
        by_id[checklist_id]["passed"] = passed
        by_id[checklist_id]["note"] = note
        merged = list(by_id.values())

        total_passed  = sum(1 for r in merged if r.get("passed") is True)
        total_failed  = sum(1 for r in merged if r.get("passed") is False)
        total_pending = sum(1 for r in merged if r.get("passed") is None)
        new_status = "blocked" if total_failed > 0 else ("open" if total_pending > 0 else "done")

        conn.execute(
            "UPDATE review_runs SET result=?, status=?, updated_at=datetime('now') WHERE id=?",
            (json.dumps(merged), new_status, review_task_id),
        )
        conn.commit()

    _log.info("[submit_review_item] run=%s item=%s passed=%s pending=%d", review_task_id, checklist_id, passed, total_pending)
    return {"ok": True, "review_task_id": review_task_id, "item": checklist_id, "status": new_status, "passed": total_passed, "failed": total_failed, "pending": total_pending}


def handle_get_review_result(review_task_id: str) -> dict:
    """Return stored result JSON for a review run.

    Args:
        review_task_id: Id of the review_runs row.
    """
    with _connect() as conn:
        row = conn.execute(
            "SELECT id, status, result FROM review_runs WHERE id=?", (review_task_id,)
        ).fetchone()
    if row is None:
        return {"error": f"review run '{review_task_id}' not found"}
    results = json.loads(row["result"]) if row["result"] else []
    return {"review_task_id": review_task_id, "status": row["status"], "results": results}
