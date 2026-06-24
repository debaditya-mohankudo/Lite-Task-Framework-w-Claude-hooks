---
tags: jira hierarchy, issue types, epic, story, task, subtask, parent_id, tasks__create, tasks__create_epic, task-create skill, issue hierarchy, task decomposition, frozen epics, linked list, parent tracking
---
# Jira Issue Hierarchy

This document is the canonical reference for issue types, parent-child rules, and how to create tasks at each level of the hierarchy.

---

## Issue type hierarchy

```
epic
 └── story | task | bug
              └── subtask
```

| issue_type | must have parent | valid parent types |
| --- | --- | --- |
| `epic` | no | — |
| `story` | yes | `epic` |
| `task` | yes | `epic` |
| `bug` | yes | `epic` |
| `subtask` | yes | `story`, `task`, `bug` |

These rules are enforced at tool-call time by [`JiraHierarchyGate`](gates.md#jirahierarchygate----tasks__create) — violations are denied before `tasks__create` executes.

---

## `parent_id` column (source of truth)

`open_tasks.parent_id` is a self-referencing FK:

```sql
parent_id TEXT DEFAULT NULL REFERENCES open_tasks(id)
```

- All MCP tool queries use `parent_id` directly — no tag parsing.
- The `parent:<id>` tag is still written on create and kept for display, but is **never** used in DB queries.
- Existing DBs without the column are migrated automatically on first `tasks__list` call (backfill from `parent:<id>` tags).

---

## How to create tasks at each level

> **Shortcut:** use the `/task-create` skill before calling `tasks__create` — it summarises hierarchy rules, required args, and body templates in one step.

### Epic (no parent)

```python
mcp__claude-hooks__tasks__create(
    title="Auth system overhaul",
    body="...",
    issue_type="epic",
    cwd="/path/to/repo",   # or domain="macos" for non-dev work
)
```

### Story / Task / Bug (parent must be epic)

```python
epic_id = "<id from epic create>"

mcp__claude-hooks__tasks__create(
    title="Implement OAuth2 flow",
    body="...",
    issue_type="story",   # or "task" / "bug"
    parent_id=epic_id,
    cwd="/path/to/repo",
)
```

### Subtask (parent must be story, task, or bug)

```python
story_id = "<id from story create>"

mcp__claude-hooks__tasks__create(
    title="Write token refresh logic",
    body="...",
    issue_type="subtask",
    parent_id=story_id,
    cwd="/path/to/repo",
)
```

---

## Typical decomposition workflow

```
1. Create epic            → no parent_id
2. Create stories/tasks   → parent_id = epic.id
3. Activate first story   → tasks__set_active(story_id, session_id)
4. Work + /gc             → commits tagged task:<story_id>
5. story:<id> done        → auto-close; move to next story
6. All stories done       → epic auto-closes
```

> **Rule:** Activate only stories/tasks/bugs for day-to-day work. Epics are umbrellas — never activated directly.
>
> **Tip:** Run `/task-create` before creating a new artifact to get the correct body template and a hierarchy reminder inline.

---

## Gate enforcement summary

`JiraHierarchyGate` fires on every `tasks__create` call:

| Condition | Result |
| --- | --- |
| `epic` + no parent_id | **allow** |
| `epic` + parent_id set | **deny** — epics cannot have a parent |
| `story`/`task`/`bug` + no parent_id | **deny** — parent required |
| `story`/`task`/`bug` + parent is epic | **allow** |
| `story`/`task`/`bug` + parent is not epic | **deny** — wrong parent type |
| `subtask` + no parent_id | **deny** — parent required |
| `subtask` + parent is story/task/bug | **allow** |
| `subtask` + parent is epic | **deny** — wrong parent type |
| DB error during lookup | **allow** (fail-open) |
| parent_id not found in DB | **deny** — create parent first |

---

## `tasks__list` tree output

`tasks__list` renders tasks as a DFS tree ordered by parent-child relationships:

```
[epic]    Auth system overhaul           depth=0
[story]     Implement OAuth2 flow        depth=1
[subtask]     Write token refresh logic  depth=2
[subtask]     Add PKCE support           depth=2
[bug]       Fix session expiry race      depth=1
```

- `depth` field is included in every row (0 = root/epic, 1 = child, 2 = grandchild).
- Parents filtered by status (e.g. `done`) are fetched as context-only nodes so children still appear at the correct depth.
- Cycles in `parent_id` are detected and emitted at `depth=0` without crashing.

---

← [Architecture](../ARCHITECTURE.md) · [Task Framework](task_framework.md) · [Gates](gates.md)
