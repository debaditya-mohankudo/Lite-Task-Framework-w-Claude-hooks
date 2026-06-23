import json
import re
import sqlite3
import sys
from pathlib import Path

from config import config
from src.logger import get_logger

# Ensure repo root is on path so scripts.build_memories_embeddings resolves
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

_log = get_logger(__name__)

# All user-supplied values (name, type, domain, tags, body, query) are passed via
# parameterized queries (? placeholders) — SQL injection is not possible here.

MEMORY_DB      = config.memory_db
TOOL_HINTS_DB  = config.tool_hints_db
VALID_TYPES    = set(config.memory_valid_types)


def handle_add(
    name: str,
    type: str,
    body: str,
    domain: str = "global",
    tags: str = "",
) -> dict:
    """Insert or update a memory in MEMORY.sqlite.

    Args:
        name:   Unique slug (kebab-case). Existing entry is overwritten.
        type:   One of: user, feedback, project, reference.
        body:   Memory content. For feedback/project include Why: and How to apply: lines.
        domain: Any string domain (e.g. global, macos, health, market-intel).
        tags:   Comma-separated keywords for retrieval scoring.
    """
    if type not in VALID_TYPES:
        _log.warning("handle_add rejected invalid type '%s' for name='%s'", type, name)
        return {"error": f"Invalid type '{type}'. Must be one of: {', '.join(VALID_TYPES)}"}

    with sqlite3.connect(MEMORY_DB) as con:
        con.row_factory = sqlite3.Row
        con.execute(
            """
            INSERT INTO memories (name, type, domain, tags, body, updated)
            VALUES (?, ?, ?, ?, ?, datetime('now'))
            ON CONFLICT(name) DO UPDATE SET
                type=excluded.type,
                domain=excluded.domain,
                tags=excluded.tags,
                body=excluded.body,
                updated=excluded.updated
            """,
            (name, type, domain, tags, body),
        )
    _log.info("memory upserted: name='%s' type=%s domain=%s", name, type, domain)
    try:
        from scripts.build_memories_embeddings import upsert_memories
        upsert_memories([name])
    except Exception as exc:
        _log.warning("memory vector upsert failed for '%s': %s", name, exc)
    return {"ok": True, "name": name, "action": "upserted"}


def _normalize_slug(s: str) -> str:
    """Strip hyphens and underscores for slug-insensitive comparison."""
    return s.replace("-", "").replace("_", "")


def _search_rows(query: str, type: str, domain: str, con: sqlite3.Connection) -> list:
    like = f"%{query}%"
    norm = f"%{_normalize_slug(query)}%"
    # REPLACE(name,'-','') and REPLACE(...,'_','') lets SQLite compare normalized slugs
    sql = (
        "SELECT id, name, type, domain, tags, body, updated FROM memories "
        "WHERE (name LIKE ? OR REPLACE(REPLACE(name,'-',''),'_','') LIKE ? "
        "OR tags LIKE ? OR body LIKE ?)"
    )
    params: list = [like, norm, like, like]
    if type:
        sql += " AND type = ?"
        params.append(type)
    if domain:
        sql += " AND domain = ?"
        params.append(domain)
    sql += " ORDER BY updated DESC LIMIT 20"
    return con.execute(sql, params).fetchall()


def handle_search(query: str, type: str = "", domain: str = "") -> dict:
    """Search memories by keyword across name, tags, and body.

    Slug normalization: underscores and hyphens are stripped before matching
    the name column, so 'claude_hooks_goals' matches 'claude-hooks-goals'.

    If the full query returns no results and contains multiple words, retries
    with each individual keyword and unions the results.

    Args:
        query:  Keyword(s) to search for (case-insensitive).
        type:   Optional filter by type (user/feedback/project/reference).
        domain: Optional filter by domain.
    """
    with sqlite3.connect(MEMORY_DB) as con:
        con.row_factory = sqlite3.Row
        rows = _search_rows(query, type, domain, con)

        if not rows:
            # Split on whitespace AND on slug separators so 'claude_hooks' → ['claude', 'hooks']
            raw_tokens = query.replace("_", " ").replace("-", " ").split()
            tokens = [t for t in raw_tokens if len(t) > 2]
            if len(tokens) > 1:
                seen: dict[int, sqlite3.Row] = {}
                for token in tokens:
                    for row in _search_rows(token, type, domain, con):
                        seen.setdefault(row["id"], row)
                rows = sorted(seen.values(), key=lambda r: r["updated"], reverse=True)

    _log.debug("handle_search query='%s' type=%s domain=%s → %d results", query, type, domain, len(rows))
    return {
        "count": len(rows),
        "results": [dict(r) for r in rows],
    }


def handle_list(type: str = "", domain: str = "") -> dict:
    """List all memories, optionally filtered by type or domain.

    Args:
        type:   Optional filter by type (user/feedback/project/reference).
        domain: Optional filter by domain.
    """
    sql = "SELECT id, name, type, domain, tags, updated FROM memories WHERE 1=1"
    params: list = []

    if type:
        sql += " AND type = ?"
        params.append(type)
    if domain:
        sql += " AND domain = ?"
        params.append(domain)

    sql += " ORDER BY updated DESC"

    with sqlite3.connect(MEMORY_DB) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(sql, params).fetchall()

    return {
        "count": len(rows),
        "memories": [dict(r) for r in rows],
    }


def handle_get(name: str) -> dict:
    """Get the full body of a single memory by name.

    Args:
        name: The memory slug (exact match).
    """
    with sqlite3.connect(MEMORY_DB) as con:
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT * FROM memories WHERE name = ?", (name,)
        ).fetchone()

    if not row:
        _log.warning("handle_get: no memory found with name='%s'", name)
        return {"error": f"No memory found with name '{name}'"}
    _log.debug("handle_get: fetched name='%s'", name)
    return dict(row)


def handle_list_domains(domains: str, type: str = "") -> dict:
    """List memories from multiple domains in one call.

    Args:
        domains: Comma-separated domain names (e.g. "astrology,global").
        type:    Optional filter by type (user/feedback/project/reference).
    """
    domain_list = [d.strip() for d in domains.split(",") if d.strip()]
    if not domain_list:
        return {"error": "No domains provided"}

    placeholders = ",".join("?" * len(domain_list))
    sql = f"SELECT id, name, type, domain, tags, updated FROM memories WHERE domain IN ({placeholders})"
    params: list = list(domain_list)

    if type:
        sql += " AND type = ?"
        params.append(type)

    sql += " ORDER BY updated DESC"

    with sqlite3.connect(MEMORY_DB) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(sql, params).fetchall()

    return {
        "count": len(rows),
        "domains": domain_list,
        "memories": [dict(r) for r in rows],
    }


def handle_tool_hints(domain: str = "", top_n: int = 20) -> dict:
    """Show MCP tool usage stats from tool_hints.sqlite.

    Args:
        domain: Optional filter by domain (e.g. "astrology", "macos").
        top_n:  Max rows to return, sorted by count descending.
    """
    if not TOOL_HINTS_DB.exists():
        return {"error": "tool_hints.sqlite not found — no tool calls logged yet."}

    sql = "SELECT tool_name, domain, count, last_used, avg_latency_ms, keywords, skill FROM mcp_tool_hints"
    params: list = []

    if domain:
        sql += " WHERE domain = ?"
        params.append(domain)

    sql += " ORDER BY count DESC LIMIT ?"
    params.append(top_n)

    with sqlite3.connect(TOOL_HINTS_DB) as con:
        con.row_factory = sqlite3.Row
        try:
            rows = con.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return {"error": "mcp_tool_hints table not found — no tool calls logged yet."}

    return {
        "count": len(rows),
        "tools": [dict(r) for r in rows],
    }




def handle_read_compact(session_id: str) -> dict:
    """Read the compact summary written by /compact for a given session.

    Scans the session's JSONL file for the last continuation-summary message
    injected by Claude Code's /compact command.

    Args:
        session_id: The Claude Code session ID (UUID).
    """
    projects_root = Path.home() / ".claude" / "projects"

    # Find the JSONL — it lives under <projects_root>/<slug>/<session_id>.jsonl
    matches = list(projects_root.rglob(f"{session_id}.jsonl"))
    if not matches:
        return {"error": f"No JSONL found for session_id '{session_id}'"}

    jsonl_path = matches[0]
    summary_text: str | None = None

    _COMPACT_MARKER = "This session is being continued from a previous conversation"

    with jsonl_path.open() as fh:
        for raw in fh:
            raw = raw.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if obj.get("type") != "user":
                continue
            content = obj.get("message", {}).get("content", "")
            if isinstance(content, str) and content.startswith(_COMPACT_MARKER):
                summary_text = content  # keep last occurrence

    if summary_text is None:
        return {"error": "No compact summary found in this session's JSONL"}

    # Extract just the Summary block (between "Summary:" and the next top-level section)
    m = re.search(r"^Summary:\n(.*?)(?=\nIf you need specific details|\Z)", summary_text, re.DOTALL | re.MULTILINE)
    summary_body = m.group(1).strip() if m else summary_text

    return {
        "session_id": session_id,
        "jsonl_path": str(jsonl_path),
        "summary": summary_body,
    }


def handle_delete(name: str) -> dict:
    """Delete a memory by name.

    Args:
        name: The memory slug to delete.
    """
    with sqlite3.connect(MEMORY_DB) as con:
        con.row_factory = sqlite3.Row
        cur = con.execute("DELETE FROM memories WHERE name = ?", (name,))

    if cur.rowcount == 0:
        _log.warning("handle_delete: no memory found with name='%s'", name)
        return {"error": f"No memory found with name '{name}'"}
    _log.info("memory deleted: name='%s'", name)
    try:
        from scripts.build_memories_embeddings import remove_memory
        remove_memory(name)
    except Exception as exc:
        _log.warning("memory vector remove failed for '%s': %s", name, exc)
    return {"ok": True, "deleted": name}
