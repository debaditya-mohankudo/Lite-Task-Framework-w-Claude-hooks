"""MCP tools for task management — proj_tasks.db in ~/.claude/.

Migrated from claude_for_mac_local to make claude-hooks self-contained.
Covers full task lifecycle (CRUD, activation, history) and typed task relations.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import uuid
from pathlib import Path
from typing import Optional

_DB = Path.home() / ".claude" / "proj_tasks.db"
_TASK_ACTIVATE_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "task_activate.py"
_HOOKS_ROOT = Path(__file__).resolve().parents[3]

_STOPWORDS = {
    "the", "and", "for", "this", "that", "with", "from", "have", "been",
    "will", "are", "was", "but", "not", "can", "also", "all", "its", "our",
    "when", "then", "into", "onto", "over", "make", "need", "use", "get",
    "set", "add", "new", "via", "per", "any", "how", "what", "where", "who",
}

_RELATION_TYPES = {"related_to", "duplicate_of", "caused_by", "blocks", "blocked_by"}

_INVERSE: dict[str, str | None] = {
    "blocks":       "blocked_by",
    "blocked_by":   "blocks",
    "caused_by":    None,
    "duplicate_of": None,
    "related_to":   None,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_task_script(args: list[str]) -> dict:
    """Shell out to task_activate.py in the claude-hooks venv (where langgraph lives)."""
    try:
        result = subprocess.run(
            ["uv", "run", "python", str(_TASK_ACTIVATE_SCRIPT)] + args,
            capture_output=True, text=True, timeout=30,
            cwd=str(_HOOKS_ROOT),
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            return {"error": stderr or f"script exited {result.returncode}"}
        return json.loads(result.stdout.strip())
    except Exception as e:
        return {"error": str(e)}


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


_ISSUE_TYPES = {"epic", "story", "task", "bug", "subtask"}


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
            issue_type TEXT DEFAULT 'task',
            parent_id  TEXT DEFAULT NULL REFERENCES open_tasks(id),
            created_at TIMESTAMP DEFAULT (datetime('now')),
            updated_at TIMESTAMP DEFAULT (datetime('now'))
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS task_edges (
            from_id       TEXT NOT NULL,
            to_id         TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            created_at    TIMESTAMP DEFAULT (datetime('now')),
            PRIMARY KEY (from_id, to_id, relation_type)
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


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB), timeout=5)
    conn.row_factory = sqlite3.Row
    _ensure_db(conn)
    _migrate(conn)
    return conn


# ---------------------------------------------------------------------------
# Task CRUD
# ---------------------------------------------------------------------------

def handle_create(title: str, body: str = "", cwd: str = "", domain: str = "", parent_id: str = "", session_id: str = "", issue_type: str = "task") -> dict:
    """Create a new open task with auto-generated tags. Returns the task id.

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
            if row["status"] == "done":
                return {"error": f"Parent task '{parent_id}' is already done"}
        conn.execute(
            "INSERT INTO open_tasks (id, title, body, tags, issue_type, parent_id) VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, title.strip(), body.strip(), tags, issue_type, parent_id or None),
        )
    if session_id:
        _run_task_script(["append", task_id, session_id])
    return {"id": task_id, "title": title, "tags": tags, "status": "open", "issue_type": issue_type}


def handle_list(status: str = "open,wip", limit: int = 50) -> list:
    """List tasks filtered by status (comma-separated). Default: open and wip.

    Tasks are returned in DFS tree order (parent → children → grandchildren).
    Each task includes a 'depth' field (0 = root, 1 = child, 2 = grandchild, …).
    Tasks whose parent is not in the result set are fetched from DB and shown at
    their natural depth; orphans (parent truly missing) appear at depth 0.

    Args:
        status: Comma-separated statuses to include. Values: open, wip, done.
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
    """Return a single task by id, including its relation edges.

    Args:
        id: Task id.
    """
    with _connect() as conn:
        row = conn.execute("SELECT * FROM open_tasks WHERE id = ?", (id,)).fetchone()
        if row is None:
            return {"error": f"Task '{id}' not found"}
        task = _task_row(row)
        edges = conn.execute("""
            SELECT e.to_id AS neighbour, e.relation_type, 'outgoing' AS direction, t.title, t.status
            FROM task_edges e JOIN open_tasks t ON t.id = e.to_id WHERE e.from_id = ?
            UNION
            SELECT e.from_id AS neighbour, e.relation_type, 'incoming' AS direction, t.title, t.status
            FROM task_edges e JOIN open_tasks t ON t.id = e.from_id WHERE e.to_id = ?
              AND NOT EXISTS (SELECT 1 FROM task_edges e2 WHERE e2.from_id = ? AND e2.to_id = e.from_id)
            ORDER BY relation_type, direction
        """, (id, id, id)).fetchall()
    task["relations"] = [dict(e) for e in edges]
    return task


def handle_update(id: str, title: str = "", body: str = "", status: str = "", issue_type: str = "", tags: str = "") -> dict:
    """Update task fields. Only provided fields are changed.

    Args:
        id:         Task id.
        title:      New title (optional).
        body:       New or appended body text (optional).
        status:     New status: open, wip, done (optional).
        issue_type: New issue type: epic, story, task, bug, subtask (optional).
        tags:       Comma-separated tags to append to existing tags (optional).
    """
    if issue_type and issue_type not in _ISSUE_TYPES:
        return {"error": f"Invalid issue_type '{issue_type}'. Valid: {sorted(_ISSUE_TYPES)}"}
    with _connect() as conn:
        row = conn.execute("SELECT * FROM open_tasks WHERE id = ?", (id,)).fetchone()
        if row is None:
            return {"error": f"Task '{id}' not found"}
        new_title      = title.strip()      if title      else row["title"]
        new_body       = body.strip()       if body       else row["body"] or ""
        new_status     = status.strip()     if status     else row["status"]
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
    return {"ok": True, "id": id, "status": new_status, "issue_type": new_issue_type, "tags": new_tags}


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
    if session_id:
        _run_task_script(["remove", id, session_id])
    return {"ok": True, "id": id, "status": "abandoned"}


def handle_search(query: str, status: str = "open,wip") -> list:
    """Full-text keyword search over task titles, bodies, and tags.

    Args:
        query:  Space-separated keywords.
        status: Comma-separated statuses to search within. Default: open,wip.
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
# Task activation (shells out to task_activate.py / langgraph)
# ---------------------------------------------------------------------------

def handle_set_active(task_id: str, session_id: str) -> dict:
    """Activate a task for this session via the task_graph (task_activate branch).

    Shells out to scripts/task_activate.py in the claude-hooks venv (where langgraph
    lives) to write active_task_id + task_memories into the LangGraph checkpoint.
    The next UPS turn inherits them from the checkpoint.

    Args:
        task_id:    Task id to activate.
        session_id: Current Claude session_id (from Turn state in system prompt).
    """
    return _run_task_script(["activate", task_id, session_id])


def handle_clear_active(session_id: str) -> dict:
    """Clear the active task for this session.

    Args:
        session_id: Current Claude session_id.
    """
    return _run_task_script(["clear", session_id])


def handle_pop_active(session_id: str) -> dict:
    """Pop the previous task from the stack and re-activate it.

    If the stack is empty, the active task is cleared instead.

    Args:
        session_id: Current Claude session_id.
    """
    return _run_task_script(["pop", session_id])


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
    """Explicitly mark a task as done and clear it from the session checkpoint.

    Marks status='done', logs a final event, zeros active_task_id in the checkpoint.
    Auto-closes the parent task if all its subtasks are now done.

    Args:
        task_id:    Task id to finish.
        session_id: Current Claude session_id (from Turn state in system prompt).
        reason:     Optional one-line summary of what was accomplished.
    """
    if not _DB.exists():
        return {"error": "proj_tasks.db not found"}
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

    _run_task_script(["finish", task_id, session_id])
    clear_result = _run_task_script(["clear", session_id])
    out: dict = {"ok": True, "task_id": task_id, "status": "done"}
    if parent_closed:
        out["parent_closed"] = parent_closed
    if "error" in clear_result:
        out["checkpoint_warning"] = clear_result["error"]
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
    if session_id:
        _run_task_script(["decision", task_id, session_id, decision.strip()])
    return {"logged": task_id, "decision": decision.strip()}


def handle_history(id: str) -> list:
    """Return all logged events for a task in chronological order.

    Args:
        id: Task id.
    """
    with _connect() as conn:
        rows = conn.execute(
            """SELECT id, task_id, prompt_id, session_id, turn, summary, tools, related, logged_at
               FROM task_events WHERE task_id = ? ORDER BY logged_at ASC""",
            (id,),
        ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Task relations (typed edges)
# ---------------------------------------------------------------------------

def handle_relate(from_id: str, to_id: str, relation_type: str) -> dict:
    """Add a typed relation edge between two tasks.

    Symmetric inverses (blocks ↔ blocked_by) are written automatically.
    Both tasks must exist in proj_tasks.db.

    Args:
        from_id:       Source task id.
        to_id:         Target task id.
        relation_type: One of: related_to, duplicate_of, caused_by, blocks, blocked_by.
    """
    if relation_type not in _RELATION_TYPES:
        return {"ok": False, "error": f"unknown relation_type '{relation_type}'. Valid: {sorted(_RELATION_TYPES)}"}
    if not _DB.exists():
        return {"ok": False, "error": "proj_tasks.db not found"}
    with _connect() as conn:
        for tid in (from_id, to_id):
            if not conn.execute("SELECT id FROM open_tasks WHERE id=?", (tid,)).fetchone():
                return {"ok": False, "error": f"task not found: {tid}"}
        conn.execute(
            "INSERT OR REPLACE INTO task_edges (from_id, to_id, relation_type) VALUES (?,?,?)",
            (from_id, to_id, relation_type),
        )
        inverse = _INVERSE.get(relation_type)
        if inverse:
            conn.execute(
                "INSERT OR REPLACE INTO task_edges (from_id, to_id, relation_type) VALUES (?,?,?)",
                (to_id, from_id, inverse),
            )
        conn.commit()
    return {"ok": True, "from_id": from_id, "to_id": to_id, "relation_type": relation_type}


def handle_unrelate(from_id: str, to_id: str, relation_type: str = "") -> dict:
    """Remove a relation edge (and its automatic inverse, if any).

    Args:
        from_id:       Source task id.
        to_id:         Target task id.
        relation_type: Specific type to remove. If empty, removes all edges between the pair.
    """
    if not _DB.exists():
        return {"ok": False, "error": "proj_tasks.db not found"}
    with _connect() as conn:
        if relation_type:
            conn.execute(
                "DELETE FROM task_edges WHERE from_id=? AND to_id=? AND relation_type=?",
                (from_id, to_id, relation_type),
            )
            inverse = _INVERSE.get(relation_type)
            if inverse:
                conn.execute(
                    "DELETE FROM task_edges WHERE from_id=? AND to_id=? AND relation_type=?",
                    (to_id, from_id, inverse),
                )
        else:
            conn.execute(
                "DELETE FROM task_edges WHERE (from_id=? AND to_id=?) OR (from_id=? AND to_id=?)",
                (from_id, to_id, to_id, from_id),
            )
        conn.commit()
    return {"ok": True}


def handle_neighbors(task_id: str) -> list:
    """Return all relation edges touching task_id (both directions).

    Each entry: neighbour (task id), relation_type, direction (outgoing/incoming), title, status.

    Args:
        task_id: Task to query.
    """
    if not _DB.exists():
        return []
    with _connect() as conn:
        rows = conn.execute("""
            SELECT e.to_id AS neighbour, e.relation_type, 'outgoing' AS direction,
                   t.title, t.status
            FROM task_edges e JOIN open_tasks t ON t.id = e.to_id
            WHERE e.from_id = ?
            UNION
            SELECT e.from_id AS neighbour, e.relation_type, 'incoming' AS direction,
                   t.title, t.status
            FROM task_edges e JOIN open_tasks t ON t.id = e.from_id
            WHERE e.to_id = ?
              AND NOT EXISTS (
                  SELECT 1 FROM task_edges e2
                  WHERE e2.from_id = ? AND e2.to_id = e.from_id
              )
            ORDER BY relation_type, direction
        """, (task_id, task_id, task_id)).fetchall()
    return [dict(r) for r in rows]
