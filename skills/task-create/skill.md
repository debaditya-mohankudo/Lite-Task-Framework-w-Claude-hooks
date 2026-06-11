---
name: task-create
description: Quick reference for creating tasks correctly — which args to pass, when to use cwd vs domain, how to handle subtasks. Use when about to call tasks__create or when the user says /task-create.
user-invocable: true
wiki: "[[Documentation/Tools/claude-hooks/task_framework.md]]"
---

Reference for `mcp__claude-hooks__tasks__create`. Read this before calling it.

## Signatures

```python
# Dev task — cwd auto-detects project name from pyproject.toml + domain from cwd_map
mcp__claude-hooks__tasks__create(
    title="<short title>",
    body="<Task:/Resolution:/Cause:/Files: formatted>",
    cwd="<absolute path to repo>",
)

# Research / non-dev task — explicit domain, no cwd
mcp__claude-hooks__tasks__create(
    title="<short title>",
    body="<Task:/Resolution:/Cause:/Files: formatted>",
    domain="<domain>",
)

# Subtask — always pass parent_id
mcp__claude-hooks__tasks__create(
    title="<short title>",
    body="<Task:/Resolution:/Cause:/Files: formatted>",
    cwd="<repo path>",           # or domain=
    parent_id="<parent_task_id>",
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

```
Task:
<one-line goal>

Resolution:
<what fixed / solved it — fill in after done>

Cause:
<root cause or research finding>

Files:
<file1>, <file2>  ← leave blank for research tasks
```

## Rules

- **Never pass both `cwd` and `domain`** — `domain` takes precedence; pick one.
- **cwd for dev, domain for everything else.**
- For market-intel research, always use `domain="market-intel"` — never pass a k-mirror path as cwd.
- Always activate after creating: `tasks__set_active(task_id, session_id)`.
