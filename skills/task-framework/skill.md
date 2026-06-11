---
name: task-framework
description: Start or resume a task using the task graph framework. Creates a task, activates it for the session, and explains the lifecycle. Use when the user runs /task-framework or asks to work on a task with tracking.
user-invocable: true
wiki: "[[Documentation/Tools/claude-hooks/task_framework.md]]"
---

<!-- source of truth: vault Documentation/Tools/claude-hooks/task_framework.md -->

You are now operating in task-framework mode. Read these instructions carefully — they define how to use the task graph throughout this session.

## What the task framework does

Every task you work on has a lifecycle tracked in `proj_tasks.db`:

```
tasks__create      →  task is open
tasks__set_active  →  task becomes wip; session_id bound in checkpoint
  (each UPS turn)  →  task history injected into your system prompt automatically
/gc                →  commit while task is active (appends task:<id> to commit body)
task:<id> done     →  auto-closes task at session stop (keyword detection)
tasks__finish      →  explicit close with reason
```

The active task's turn history (up to 5 turns: summary + tools used) is injected into your `## Task history (this session)` system prompt block automatically — you don't need to ask for it.

## Getting the session_id

The session_id is always in your `## Turn state` system prompt block:

```
## Turn state
- session_id: <uuid>
- prompt_id: <uuid>
```

There is no MCP tool for this — always read from `## Turn state`.

## Steps when invoked with a task description

### 0. Assess decomposition

Before creating, assess if the task has 2–3 clearly distinct phases that can be worked sequentially. If yes:
- Propose the subtask list to the user (one line each) with the intended sequence
- Get confirmation before creating
- **Create a parent task first** (e.g. `"Portfolio DB — implement JSON storage"`)
- Create each subtask passing `parent_id=<parent_task_id>` — this tags them `parent:<id>` automatically, enabling hierarchy display and auto-close of parent when all subtasks are done
- Activate the first subtask; work sequentially

If the task is a single coherent piece of work, skip this step and create one task directly. Don't force a split.

### 1. Create the task

```python
mcp__claude-hooks__tasks__create(title="<short title>", body="<context / plan>", cwd="<current working directory>")
# returns: {"id": "<task_id>", ...}
# cwd enables automatic project:<name> tagging from pyproject.toml
```

### 2. Activate it for this session

```python
mcp__claude-hooks__tasks__set_active(task_id="<task_id>", session_id="<session_id from Turn state>")
```

If you get `No module named langgraph` (MCP env issue), fall back to the script:

```bash
cd ~/workspace/claude-hooks && uv run python scripts/task_activate.py activate <task_id> <session_id>
```

### 3. Confirm to the user

```
Task task:<id> active — <title>
Tracking turns and tools for this session. Say "task:<id> done" when finished.
```

### 4. Work on the task normally

- Use `tasks__list()` to see open/wip tasks (add `limit=N` if you need more than the default 50)
- Use `tasks__history(id)` to inspect logged turn events
- Use `tasks__update(id, body=...)` to append notes mid-task

**Finding code while working:**
```python
# Hybrid FTS + semantic — use this first
mcp__claude-hooks__code_rag__smart_search(query="<symbol or concept>", repo="<abs path>")

# Pure semantic fallback
mcp__claude-hooks__code_rag__query(query="<natural language>", repo="<abs path>")

# Reindex after editing files (keeps search current)
mcp__claude-hooks__code_rag__index_files(files=["relative/path/to/file.py"], repo="<abs path>")
```
`repo` defaults to `claude-hooks` if omitted. Pass the absolute repo path when working in another project.

### 5. Commit before closing

**Use `/gc` for each subtask commit during the workflow** — it commits without pushing and without interrupting flow. While the task is still active, `## Active task` is visible in the system prompt and `/gc` will automatically append `task:<id>` to the commit message body.

**Push manually after the parent task is closed** — run `git push` once all subtasks are done and the parent task is marked done.

If you close the task first, the active task is cleared and the commit loses its task reference.

Order: **implement → `/gc` (per subtask) → close parent task → `git push`**

### 6. Closing the task

**Preferred — say it in your message to the user:**
```
task:<id> done
```
The stop hook detects this and auto-closes + clears the checkpoint.

**Explicit — call the finish tool:**
```python
mcp__claude-hooks__tasks__finish(task_id="<id>", session_id="<session_id>", reason="<what was accomplished>")
```

**Manual fallback:** `tasks__update(id, status="done")` then `uv run python scripts/task_activate.py clear <session_id>`

## Steps when invoked without a task description

List open tasks: `mcp__claude-hooks__tasks__list()` — display and ask which to activate or whether to create a new one.

## Rules

- **Create and activate a task before any code change.** Call `tasks__create` then `tasks__set_active` before the first Edit/Write/Bash call. No exceptions — even for one-liners.
- **One active task per session.** If `tasks__set_active` returns an error about an existing active task, call `tasks__clear_active` first (or use the script).
- **Never guess the session_id.** Always read it from `## Turn state` or `session__current()`.
- **task_memories are scored automatically** on activation — you don't need to load them manually.
- **task_context is hybrid-scoped** — session turns if ≥5 exist, else last 5 cross-session. Always oldest-first.
- Mark tasks `done` promptly. Stale `wip` tasks accumulate stale memories.
- **Work tasks sequentially.** Complete and close one task before activating the next — don't parallelize unless tasks are fully independent with no shared state.
- **Commit before closing.** Always run `/gc` while the task is still active so the commit body gets `task:<id>` appended automatically. Push manually after the parent task closes.
