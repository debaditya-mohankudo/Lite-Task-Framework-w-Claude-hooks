---
tags: task UI, task manager, web UI, frontend, task list, task tree, epic, story, subtask, UI layout, task status, task decisions, session history, task web interface, memory browser, docs browser, search, HTMX, Jinja2
---
# Task Manager Web UI

Canonical doc for the browser UI that surfaces `proj_tasks.db`, `MEMORY.sqlite`, and `docs/` over HTTP.
Update this doc as each story ships.

**Epic:** `task:78c06f7f`
**Entry point:** `http://localhost:8766/ui/`

---

## Architecture

The UI is mounted on the existing hook server (port 8766) via a FastAPI `APIRouter`.

```text
hooks/server.py              ← hook server; mounts ui_router via app.include_router()
hooks/ui/
  __init__.py                ← package marker
  deps.py                    ← shared helpers: JINJA_ENV, render(), paths, get_active_session(),
                               parse_body_fields(), render_doc(), mem_list(), mem_get()
                               + JINJA_ENV.globals['urls'] — central route registry
  routes.py                  ← APIRouter with all 13 /ui/* handlers
hooks/templates/ui/
  base.html                  ← three-column shell: sidebar + detail-panel + right-panel
  tasks/
    list.html                ← extends base; task list with epic groups and status filter
  memory/
    list.html                ← extends base; memory browser with domain/type filters
    detail.html              ← memory detail partial (right-panel)
  docs/
    list.html                ← extends base; doc browser with sidebar file list
    detail.html              ← doc detail partial (right-panel)
  partials/
    sidebar.html             ← task tree, epic groups, open/done filter tabs
    task_detail.html         ← title, tags, body fields, turn history, decisions, related tasks
    create_form.html         ← new task form with dynamic body fields per issue_type
    task_body_fields.html    ← dynamic form sections swapped by HTMX on issue_type change
    cockpit.html             ← active task strip (polled every 10s)
    search_results.html      ← search overlay results (tasks + decisions + memories)
    icons.html               ← central icon macro registry
    error.html               ← error partial
hooks/static/
  ui.css                     ← minimal dark-theme styles (no build step)
```

**No inline handlers in server.py** — all `/ui/*` logic lives in `hooks/ui/routes.py`.
`hooks/ui/deps.py` is the shared dependency layer: Jinja2 env, path constants, task/doc/memory helpers.

---

## Routes

| Method | Path                                  | Returns      | Description                                    |
|--------|---------------------------------------|--------------|------------------------------------------------|
| GET    | `/ui/`                                | redirect     | → `/ui/tasks/`                                 |
| GET    | `/ui/tasks/`                          | full page    | Task list with sidebar tree                    |
| GET    | `/ui/tasks/{id}`                      | HTML partial | Task detail (HTMX → `#right-panel`)            |
| GET    | `/ui/tasks/new`                       | HTML partial | Create form (HTMX → `#right-panel`)            |
| GET    | `/ui/tasks/body-fields?issue_type=X`  | HTML partial | Dynamic form fields (HTMX → `#body-fields`)    |
| POST   | `/ui/tasks`                           | HTML partial | Create task → returns refreshed sidebar        |
| GET    | `/ui/sidebar?status=open\|done`       | HTML partial | Filtered sidebar (HTMX swap)                   |
| GET    | `/ui/cockpit`                         | HTML partial | Active task strip (polled every 10s)           |
| GET    | `/ui/search?q=`                       | HTML partial | Search overlay — tasks + decisions + memories  |
| GET    | `/ui/memory/`                         | full page    | Memory browser with domain/type filter         |
| GET    | `/ui/memory/{slug}`                   | HTML partial | Memory detail (HTMX → `#right-panel`)          |
| GET    | `/ui/docs/`                           | full page    | Docs browser with sidebar file list            |
| GET    | `/ui/docs/{slug:path}`                | HTML partial | Doc detail (HTMX → `#right-panel`)             |

**Non-UI endpoints relevant to active task:**

- `GET /session/active` — JSON `{task_id, title, session_id, turn}` or `{}`
- `GET /session/memory` — JSON event log (last N prompts + tool calls)

---

## Layout — three-column shell

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│  Lite Task Framework — For Solo Developers                                  │
│  [cockpit strip — active task, session, turn — polls every 10s]             │
├──────────────────┬──────────────────────────────┬───────────────────────────┤
│ NAVIGATION       │  detail-panel                │  right-panel              │
│  ⊞ Tasks         │  (task list / memory list /  │  (task detail /           │
│  ⌕ Search        │   doc content)               │   memory detail /         │
│  ◈ Memories      │                              │   doc panel)              │
│  ≡ Docs          │                              │  hidden by default;       │
│                  │                              │  revealed on HTMX load    │
│ TASKS / DOMAINS  │                              │                           │
│ [filter tabs]    │                              │                           │
└──────────────────┴──────────────────────────────┴───────────────────────────┘
```

- `#right-panel` starts hidden (`display: none`). An `htmx:afterSettle` listener in `base.html` adds `.is-open` when HTMX loads content into it, toggling `display: block`.
- Memory page overrides right-panel width to 480px via `body.mem-page #right-panel.is-open`.

---

## URL registry

All route paths are defined once in `hooks/ui/deps.py` and injected into every template as `urls`:

```python
JINJA_ENV.globals["urls"] = {
    "tasks":        "/ui/tasks/",
    "memory":       "/ui/memory/",
    "docs":         "/ui/docs/",
    "search":       "/ui/search",
    "cockpit":      "/ui/cockpit",
    "sidebar":      "/ui/sidebar",
    "tasks_create": "/ui/tasks",
    "body_fields":  "/ui/tasks/body-fields",
    "tasks_new":    "/ui/tasks/new",
}
```

Templates use `{{ urls.tasks }}`, `{{ urls.memory }}` etc. — no hardcoded strings. Dynamic segments are concatenated: `{{ urls.tasks }}{{ task.id }}`.

---

## Icon registry

All nav icons are macros defined in `partials/icons.html` and imported in every template:

```jinja2
{% from "ui/partials/icons.html" import icon_tasks, icon_search, icon_memories, icon_docs, icon_sub %}
```

| Macro             | Symbol | Usage                      |
|-------------------|--------|----------------------------|
| `icon_tasks()`    | ⊞      | Tasks nav + list header    |
| `icon_search()`   | ⌕      | Search nav                 |
| `icon_memories()` | ◈      | Memories nav + list header |
| `icon_docs()`     | ≡      | Docs nav + list header     |
| `icon_sub()`      | —      | Subtask / sub-item rows    |

---

## Data layer

UI routes call handler functions directly — no MCP overhead:

```python
from src.tools.tasks import handle_list, handle_get, handle_history, handle_neighbors, handle_create, handle_search
```

Active session state comes from the live MemorySaver via `get_active_session()` in `deps.py` — no DB round-trip.

---

## Frontend stack

- **HTMX** (CDN) — partial swaps for right-panel, sidebar filter, search, cockpit polling
- **Jinja2** — server-rendered templates; `auto_reload=True` (templates hot-reload on change)
- **Plain CSS** — dark theme, no build toolchain, no npm

---

## Key UX features

### Cockpit strip

Polls `/ui/cockpit` every 10s. Shows active task id, title, session, turn count. Click "View" → loads task detail into `#detail-panel`.

### Search overlay

`Cmd+K` or sidebar Search link opens the overlay. Searches tasks (open+done+abandoned), decisions, and memories in parallel. Results are HTMX-loaded with 300ms debounce. Click a result → loads detail and closes overlay.

### Task detail

- Title, status/type badges, live session indicator (from MemorySaver checkpoint)
- **Tag bar** — structured tags (`parent:`, `project:`, `domain:`, `frozen`) vs label tags
- **Body fields** — parsed from `Field: value` structure, rendered as a metadata table
- Turn history grouped by session with cross-session collapse
- Decisions log, neighbor tasks (cosine similarity), parent task link

### Memory browser

Domain filter in sidebar + type chips in header. Right-panel shows full memory body, tags, domain, type.

### Docs browser

Lists all `docs/*.md` files. Click → renders markdown to HTML in right-panel. Cross-doc `.md` links rewritten to `/ui/docs/?doc=`.

### Task status and `wip`

`wip` is **not a DB status** — the DB stores only `open`, `blocked`, `done`, `abandoned`. Active state lives in the LangGraph MemorySaver checkpoint only. The cockpit strip and `/session/active` endpoint are the canonical way to query active task state.

### Error handling

FastAPI exception handlers for HTTP errors and unhandled exceptions on `/ui/*` routes return the error partial at HTTP 200 so HTMX swaps it cleanly.

---

## Stories shipped

| Task       | Description                                                                      |
|------------|----------------------------------------------------------------------------------|
| `ed4cc656` | Scaffold — FastAPI + Jinja2 + HTMX skeleton                                      |
| `4fd3e2c4` | Sidebar with task tree and epic groups                                           |
| `f117ca05` | Task detail panel                                                                |
| `88b50c57` | Create form with dynamic body fields                                             |
| `d50aca28` | Error handling                                                                   |
| `5cb6711e` | Refactor: move `/ui/*` routes to `hooks/ui/routes.py` and `hooks/ui/deps.py`     |
| `72bf38ee` | Centralize route URLs in `JINJA_ENV.globals['urls']`                             |
| `28174094` | Move personal tool mappings from `tool_registry.py` to iCloud JSON               |
| `81ac24ea` | Add `GET /session/active` endpoint                                               |

---

## Open ideas

- Inline status update (mark done from UI)
- Real-time push via SSE when active task changes mid-session
- Edit task body from detail panel
- Auth / access control (currently localhost only)
- `/api/*` JSON routes alongside `/ui/*` HTML routes for external tool access
