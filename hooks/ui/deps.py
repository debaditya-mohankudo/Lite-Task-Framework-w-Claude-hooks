"""Shared dependencies for UI route handlers.

Imported by both hooks/server.py (for backward-compat helpers) and
hooks/ui/routes.py (the APIRouter). Keeps shared state in one place and
avoids circular imports.
"""
from __future__ import annotations

import os as _os
import re as _re
import sqlite3 as _sqlite3
from pathlib import Path

import jinja2 as _jinja2
from fastapi.responses import HTMLResponse

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Derive project root from this file: hooks/ui/deps.py → hooks/ui → hooks → project root
_HOOKS_UI_DIR = Path(__file__).resolve().parent
_HOOKS_DIR    = _HOOKS_UI_DIR.parent
_PROJECT_ROOT = _HOOKS_DIR.parent
_DOCS_DIR     = _PROJECT_ROOT / "docs"
_MEM_DB       = Path.home() / ".claude" / "MEMORY.sqlite"

# ---------------------------------------------------------------------------
# Jinja2 environment
# ---------------------------------------------------------------------------

JINJA_ENV = _jinja2.Environment(
    loader=_jinja2.FileSystemLoader(str(_HOOKS_DIR / "templates")),
    autoescape=True,
    auto_reload=True,
)

# Central URL registry — injected as `urls` into every template.
# Change a route path here; all templates update automatically.
JINJA_ENV.globals["urls"] = {
    "tasks":        "/ui/tasks/",
    "tasks_new":    "/ui/tasks/new",
    "tasks_create": "/ui/tasks",
    "body_fields":  "/ui/tasks/body-fields",
    "sidebar":      "/ui/sidebar",
    "cockpit":      "/ui/cockpit",
    "search":       "/ui/search",
    "memory":       "/ui/memory/",
    "docs":         "/ui/docs/",
}


def render(template_name: str, **ctx) -> HTMLResponse:
    """Render a Jinja2 template and return an HTMLResponse."""
    t = JINJA_ENV.get_template(template_name)
    return HTMLResponse(t.render(**ctx))


def error_partial(message: str, detail: str = "") -> HTMLResponse:
    """Render the shared error partial."""
    return render("ui/partials/error.html", message=message, detail=detail)


# ---------------------------------------------------------------------------
# Task helpers
# ---------------------------------------------------------------------------

_BODY_FIELDS = (
    "Type", "Task", "Resolution", "Motivation", "Files", "Notes",
    "Cause", "Finding", "Context",
)
_BODY_FIELD_RE: "_re.Pattern | None" = None


def parse_body_fields(body: str) -> list[dict] | None:
    """Parse 'Field: value' structured task body into a list of {label, value, is_code} dicts.

    Returns None if the body has no recognised fields.
    """
    global _BODY_FIELD_RE
    if _BODY_FIELD_RE is None:
        pattern = r"^(" + "|".join(_BODY_FIELDS) + r"):\s*"
        _BODY_FIELD_RE = _re.compile(pattern, _re.MULTILINE)

    matches = list(_BODY_FIELD_RE.finditer(body))
    if not matches:
        return None

    fields = []
    for i, m in enumerate(matches):
        label = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        raw_value = body[start:end].strip()
        code_match = _re.match(r"^```(\w*)\n(.*?)```\s*$", raw_value, _re.DOTALL)
        if code_match:
            fields.append({"label": label, "value": code_match.group(2).rstrip(), "is_code": True})
        else:
            fields.append({"label": label, "value": raw_value, "is_code": False})

    return fields if fields else None


def valid_status(s: str) -> str:
    return s if s in ("open", "done") else "open"


def get_active_session() -> dict:
    """Return the active task from the most recent session checkpoint.

    Skips done/abandoned tasks even if the checkpoint is stale.
    """
    try:
        import langchain_learning.session_graph as sg
        checkpointer = getattr(sg._graph, "checkpointer", None)
        if not checkpointer:
            return {}
        from src.tools.tasks import handle_get
        latest = next(iter(checkpointer.list(None)), None)
        if not latest:
            return {}
        state = latest.checkpoint.get("channel_values", {})
        task_id = state.get("active_task_id", "")
        if not task_id:
            return {}
        t = handle_get(task_id)
        if t.get("status") in ("done", "abandoned"):
            return {}
        return {
            "task_id": task_id,
            "title": state.get("active_task_title", ""),
            "session_id": latest.config["configurable"]["thread_id"],
            "turn": state.get("turn", 0),
        }
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Docs helpers
# ---------------------------------------------------------------------------

def render_doc(slug: str) -> tuple[str, str] | None:
    """Render docs/<slug>.md to (title, html). Returns None if not found.

    slug may contain path separators, e.g. 'arch/databases'.
    Path traversal is blocked. Cross-doc .md links are rewritten to /ui/docs/?doc=.
    """
    import markdown as _md
    from pathlib import PurePosixPath
    clean = str(PurePosixPath(slug))
    if ".." in clean:
        return None
    candidates = [_DOCS_DIR / f"{clean}.md", _DOCS_DIR / f"{clean}"]
    path = next((p for p in candidates if p.exists() and p.is_file()), None)
    if path is None:
        stem = clean.split("/")[-1]
        matches = list(_DOCS_DIR.rglob(f"{stem}.md"))
        path = matches[0] if matches else None
    if path is None:
        return None
    src = path.read_text()
    title = slug
    for line in src.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
    html = _md.markdown(src, extensions=["fenced_code", "tables", "toc"])
    html = _re.sub(
        r'href="([^"]+)\.md([^"]*)"',
        lambda m: f'href="/ui/docs/?doc={m.group(1)}{m.group(2)}"',
        html,
    )
    return title, html


def list_docs() -> list[dict]:
    """List all top-level docs/*.md files as [{slug, title}]."""
    docs = []
    for p in sorted(_DOCS_DIR.glob("*.md")):
        slug = p.stem
        title = slug
        for line in p.read_text().splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        docs.append({"slug": slug, "title": title})
    return docs


# ---------------------------------------------------------------------------
# Memory helpers
# ---------------------------------------------------------------------------

def mem_list(domain: str = "", type_filter: str = "") -> tuple[list[dict], list[str], list[str]]:
    """Query MEMORY.sqlite — returns (memories, domains, types)."""
    memories: list[dict] = []
    domains: list[str] = []
    types: list[str] = []
    if not _MEM_DB.exists():
        return memories, domains, types
    with _sqlite3.connect(str(_MEM_DB)) as mc:
        mc.row_factory = _sqlite3.Row
        domains = [r[0] for r in mc.execute(
            "SELECT DISTINCT domain FROM memories ORDER BY domain"
        ).fetchall()]
        types = [r[0] for r in mc.execute(
            "SELECT DISTINCT type FROM memories ORDER BY type"
        ).fetchall()]
        where, params = [], []
        if domain:
            where.append("domain = ?"); params.append(domain)
        if type_filter:
            where.append("type = ?"); params.append(type_filter)
        clause = ("WHERE " + " AND ".join(where)) if where else ""
        memories = [dict(r) for r in mc.execute(
            f"SELECT id, name, type, domain, tags, body, updated "
            f"FROM memories {clause} ORDER BY domain, name",
            params,
        ).fetchall()]
    return memories, domains, types


def mem_get(slug: str) -> dict | None:
    """Fetch a single memory by name slug. Returns None if not found."""
    if not _MEM_DB.exists():
        return None
    with _sqlite3.connect(str(_MEM_DB)) as mc:
        mc.row_factory = _sqlite3.Row
        row = mc.execute("SELECT * FROM memories WHERE name = ?", (slug,)).fetchone()
    return dict(row) if row else None
