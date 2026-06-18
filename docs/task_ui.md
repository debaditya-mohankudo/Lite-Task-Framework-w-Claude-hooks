# Task Manager Web UI

Canonical doc for the browser UI that surfaces `proj_tasks.db` over HTTP.
Update this doc as each story ships.

**Epic:** `task:78c06f7f`
**Entry point:** `http://localhost:8766/ui/`

---

## Architecture

The UI is mounted directly on the existing hook server (`hooks/server.py`, port 8766).
No second process — same FastAPI app, additional routes under `/ui/`.

```
hooks/server.py          ← existing hook server; UI routes added here
hooks/templates/ui/      ← Jinja2 templates
  base.html              ← two-column shell (sidebar + detail panel)
  index.html             ← extends base, renders sidebar + empty #detail-panel
  partials/
    sidebar.html         ← task tree, epic groups, open/done filter tabs
    task_detail.html     ← title, tags, description, turn history, decisions, related tasks
    create_form.html     ← new task form with dynamic body fields per issue_type
    task_body_fields.html← dynamic form sections (story/task/epic/bug/subtask)
    error.html           ← error partial (icon + message + optional detail pre)
hooks/static/
  ui.css                 ← minimal dark-theme styles (no build step)
```

**Key advantage:** UI routes can access live session state directly via
`sg._graph.checkpointer.storage` (MemorySaver, already in process) — no DB
round-trip needed for active task, current turn, or mid-session decisions.

---

## Routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/ui/` | Task list (sidebar + empty detail pane) |
| GET | `/ui/sidebar?status=open\|done` | Filtered sidebar partial (HTMX swap) |
| GET | `/ui/tasks/{id}` | Task detail partial (HTMX swap into #detail-panel) |
| GET | `/ui/tasks/new` | Create form partial (HTMX swap into #detail-panel) |
| GET | `/ui/tasks/body-fields?issue_type=X` | Dynamic form body section (HTMX swap into #body-fields) |
| POST | `/ui/tasks` | Create task → calls `handle_create()` directly |

---

## Data layer

UI routes call task handler functions directly — no MCP protocol overhead:

```python
from src.tools.tasks import handle_list, handle_get, handle_history, handle_neighbors, handle_create
```

`handle_list()` returns tasks in DFS tree order (epic → children) with `parent_id` already set —
no extra sorting needed in templates.

---

## Frontend stack

- **HTMX** (CDN) — partial swaps for sidebar filter, detail panel, and dynamic form fields
- **Jinja2** — server-rendered templates (via `jinja2.Environment` directly, not `Jinja2Templates` due to starlette 1.2.1 cache bug)
- **Plain CSS** — dark theme, no build toolchain, no npm

---

## Key UX features

### Sidebar

- Epic group headers with colour for `frozen` tag (muted blue)
- `open` / `done` status filter tabs
- WIP tasks highlighted with left border

### Task Detail

- Title, status/type badges, live session indicator
- **Tag bar** — horizontal row of colour-coded chips:
  - `frozen` → blue (`#7ba7c4`)
  - `project:*` → purple
  - `domain:*` → green
  - `parent:*` → yellow
  - generic auto-tags → grey
- Description (task body as `<pre>`)
- Turn history, decisions, related tasks (cosine similarity score)
- Click related task → loads its detail panel

### Create Form

- Type selector triggers dynamic body field swap (`#body-fields`)
- Per-type fields: story/task (Task + Motivation + Resolution), epic (Overview + Motivation), bug (Description + Steps + Expected/Actual), subtask (Task only)
- Parent picker populated from open epics/stories
- Jira hierarchy validation via `validate_jira_hierarchy()` (shared with `JiraHierarchyGate`)

### Header

- Task ID search — type any id (`abc123` or `task:abc123`), press Enter → loads detail
- `+ New` button → create form

### Error handling

- FastAPI `@app.exception_handler` for HTTP errors and unhandled exceptions on `/ui/*` routes — returns error partial at HTTP 200 so HTMX swaps it
- `htmx:responseError` listener in `base.html` catches non-2xx HTMX responses and surfaces inline

---

## Frozen epics

Mark an epic as frozen by adding the `frozen` tag:

```python
tasks__update(id="<epic-id>", tags="frozen")
```

Effect:

- Sidebar: epic row shifts to muted blue-grey (`is-frozen` CSS class)
- Detail panel: `frozen` tag chip appears in blue in the tag bar

No schema change — purely tag-based.

---

## UI layout (reference mockup)

```
┌─────────────────────────────────────────────────────────────────┐
│  ⇄ claude-hooks            [jump to task id...]      + New      │
├──────────────────┬──────────────────────────────────────────────┤
│ TASKS            │ task:7f1e                          WIP        │
│ [open] [done]    │ Replace legacy token calls with opaque...     │
│                  │                                              │
│ ▼ epic 4a1b      │ [project:claude-hooks] [domain:...] [frozen] │
│   ✓ Audit tokens │ ─────────────────────────────────────────── │
│ → Replace tokens ●│ DESCRIPTION                                  │
│   ○ Update tests │   <task body text>                           │
│                  │ ─────────────────────────────────────────── │
│ ▶ epic 2c3a 🔵   │ TURN HISTORY                                 │
│   (frozen)       │   T6  Edit·Bash  "updated session.py..."     │
│   ○ Fix rate lim │   T5  Read       "reviewed token schema..."   │
│   ○ Add logging  │ ─────────────────────────────────────────── │
│                  │ DECISIONS  1 logged                          │
│                  │ ─────────────────────────────────────────── │
│                  │ RELATED TASKS                                │
│                  │   task:2b1a  JWT audit and expiry review 0.87│
│                  │   task:5f3c  Redis setup and pooling     0.81│
└──────────────────┴──────────────────────────────────────────────┘
```

---

## Stories (linked list)

```text
task:ed4cc656  →  task:4fd3e2c4  →  task:f117ca05  →  task:88b50c57  →  task:d50aca28
   scaffold          sidebar           detail panel      create form      error handling
```

---

## Dependencies

- `jinja2` — template rendering
- `python-multipart` — form parsing for POST /ui/tasks
- `htmx` — CDN, no install needed

---

## Open questions / future ideas

- Auth / access control (currently open to localhost only via port 8766)
- Inline status update (mark wip → done from UI)
- Real-time push via SSE when active task changes mid-session
- Edit task body from detail panel
- Unfreeze via UI (remove `frozen` tag)
