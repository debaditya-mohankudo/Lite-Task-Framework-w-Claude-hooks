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
    body="<Type: + per-type template below>",
    cwd="<absolute path to repo>",
)

# Research / non-dev task — explicit domain, no cwd
mcp__claude-hooks__tasks__create(
    title="<short title>",
    body="<Type: + per-type template below>",
    domain="<domain>",
)

# Subtask — always pass parent_id
mcp__claude-hooks__tasks__create(
    title="<short title>",
    body="<Type: + per-type template below>",
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

Always start with `Type:` — pick one: `feature`, `bug`, `research`, `misc`.

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
- For market-intel research, always use `domain="market-intel"` — never pass a k-mirror path as cwd.
- Always activate after creating: `tasks__set_active(task_id, session_id)`.
