---
name: jira-task-create
description: Quick reference for creating Jira-style issues — epic, story, task, bug, subtask. Which args to pass, hierarchy rules, when to use cwd vs domain. Use when about to call tasks__create or when the user says /jira-task-create.
user-invocable: true
updated: 2026-06-11
wiki: "[[Documentation/Tools/claude-hooks/skills.md]]"
---

Reference for `mcp__claude-hooks__tasks__create`. Read this before calling it.

## Jira hierarchy

```
epic
└── story / task / bug
    └── subtask
```

- **epic** — large initiative spanning multiple sprints; never a child of another issue
- **story** — user-facing feature; child of an epic
- **task** — technical work item; child of an epic or standalone
- **bug** — something broken; child of an epic or standalone
- **subtask** — smallest unit; must have a parent (story, task, or bug)

Pass `issue_type=` to set the level. Default is `task`.

## Signatures

```python
# Epic — top-level initiative, no parent
mcp__claude-hooks__tasks__create(
    title="<initiative title>",
    body="<Type: + template below>",
    cwd="<repo path>",          # or domain=
    issue_type="epic",
)

# Story / task / bug — child of an epic
# No epic yet? Use parent_id="96c361de" (Unassigned) — move to a real epic later.
mcp__claude-hooks__tasks__create(
    title="<short title>",
    body="<Type: + template below>",
    cwd="<repo path>",          # or domain=
    parent_id="<epic_task_id>",
    issue_type="story",         # or task | bug
)

# Subtask — must have a parent (story, task, or bug)
mcp__claude-hooks__tasks__create(
    title="<short title>",
    body="<Type: + template below>",
    cwd="<repo path>",          # or domain=
    parent_id="<parent_task_id>",
    issue_type="subtask",
)

# Research / non-dev — explicit domain, no cwd
mcp__claude-hooks__tasks__create(
    title="<short title>",
    body="<Type: + template below>",
    domain="<domain>",
    issue_type="task",          # or story | bug
)
```

## domain values

| domain | When to use |
|--------|-------------|
| `market-intel` | Stock research, portfolio, FII/DII, macro, Nifty/Sensex |
| `vault` | Obsidian notes, docs, writing |
| `astrology` | Jyotish, dasha, chart analysis |
| `claude-hooks` | claude-hooks repo development |
| `macos` | macOS automation, Swift, local tools |
| `global` | Cross-domain or general |

## body format (required)

Always start with `Type:` — pick one: `feature`, `bug`, `research`, `misc`.
This is the **workflow kind** (controls required sections), separate from `issue_type`.

**feature** — new capability or enhancement
```
Type: feature
Task:
<what is being built>

Resolution:
<what was delivered — fill in after done>

Motivation:
<why this is needed>

Files:
<file1>, <file2>
```

**bug** — something broken that needs fixing
```
Type: bug
Task:
<what is broken and observed behavior>

Resolution:
<what fixed it — fill in after done>

Cause:
<root cause>

Files:
<file1>, <file2>
```

**research** — investigation, analysis, market study
```
Type: research
Task:
<question or hypothesis>

Finding:
<conclusion — fill in after done>

Context:
<what triggered this / background>

Files:
(leave blank)
```

**misc** — refactor, docs, config, cleanup
```
Type: misc
Task:
<what is being done>

Resolution:
<outcome — fill in after done>

Notes:
<any relevant context>

Files:
<file1>, <file2>
```

## Rules

- **Never pass both `cwd` and `domain`** — `domain` takes precedence; pick one.
- **cwd for dev, domain for everything else.**
- **Epics have no parent** — never pass `parent_id` for an epic.
- **No epic yet?** Use `parent_id="96c361de"` (Unassigned epic) — don't let missing hierarchy block task creation. Move to a real epic later.
- **Subtasks must have a parent** — always pass `parent_id` for `issue_type="subtask"`.
- For market-intel research, always use `domain="market-intel"` — never pass a k-mirror path as cwd.
- Always activate after creating: `tasks__set_active(task_id, session_id)`.
