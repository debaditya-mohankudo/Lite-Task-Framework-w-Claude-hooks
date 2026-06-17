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
    sidebar.html         ← task tree, epic groups, status filter tabs
    task_detail.html     ← turn history, decisions, related tasks
    create_form.html     ← new task form
hooks/static/
  ui.css                 ← minimal styles (no build step)
```

**Key advantage:** UI routes can access live session state directly via
`sg._graph.checkpointer.storage` (MemorySaver, already in process) — no DB
round-trip needed for active task, current turn, or mid-session decisions.

---

## Routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/ui/` | Task list (sidebar + empty detail pane) |
| GET | `/ui/?status=wip\|open\|all` | Filtered task list |
| GET | `/ui/tasks/{id}` | Task detail partial (HTMX swap into #detail-panel) |
| GET | `/ui/tasks/new` | Create form partial (HTMX swap into #detail-panel) |
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

- **HTMX** (CDN) — partial swaps for sidebar filter and detail panel, no full page reloads
- **Jinja2** — server-rendered templates
- **Plain CSS** — no build toolchain, no npm

---

## UI layout (reference mockup)

```
┌─────────────────────────────────────────────────────────────────┐
│  ⇄ claude-hooks                          🔍        + New        │
├──────────────────┬──────────────────────────────────────────────┤
│ TASKS            │ task:7f1e                          WIP        │
│ [all] [wip] [open│ Replace legacy token calls with opaque...     │
│                  │                                              │
│ ▼ epic 4a1b      │ ↑ epic:4a1b   session abc-12   turn 6       │
│   ✓ Audit tokens │ ─────────────────────────────────────────── │
│ → Replace tokens ●│ TURN HISTORY                                 │
│   ○ Update tests │   T6  Edit·Bash  "updated session.py..."     │
│                  │   T5  Read       "reviewed token schema..."   │
│ ▶ epic 2c3a      │   T4  Bash·Edit  "ran tests, fixed type..."  │
│   ○ Fix rate lim │ ─────────────────────────────────────────── │
│   ○ Add logging  │ DECISIONS  1 logged                          │
│                  │ ┌─────────────────────────────────────────┐ │
│                  │ │ "Chose opaque tokens over JWT — avoids  │ │
│                  │ │ key rotation complexity; Redis eviction  │ │
│                  │ │ handles expiry"           logged at T5   │ │
│                  │ └─────────────────────────────────────────┘ │
│                  │ RELATED TASKS                                │
│                  │   task:2b1a  JWT audit and expiry review 0.87│
│                  │   task:5f3c  Redis setup and pooling     0.81│
└──────────────────┴──────────────────────────────────────────────┘
```

---

## Stories (linked list)

```
task:ed4cc656  →  task:4fd3e2c4  →  task:f117ca05  →  task:88b50c57
   scaffold          sidebar           detail panel      create form
```

---

## Dependencies

- `jinja2` — template rendering
- `python-multipart` — form parsing for POST /ui/tasks
- `htmx` — CDN, no install needed

Check `pyproject.toml` before starting story 1 — add missing deps.

---

## Open questions / future ideas

- Auth / access control (currently open to localhost only via port 8766)
- Dark mode
- Inline status update (mark wip → done from UI)
- Real-time push via SSE when active task changes mid-session
