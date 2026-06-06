---
name: task-framework
description: Start or resume a task using the task graph framework. Creates a task, activates it for the session, and explains the lifecycle. Use when the user runs /task-framework or asks to work on a task with tracking.
user-invocable: true
wiki: "[[Documentation/Tools/claude-hooks/task_framework.md]]"
---

<!-- source of truth: ~/workspace/claude-hooks/docs/task_framework.md -->

You are now operating in task-framework mode. Read these instructions carefully — they define how to use the task graph throughout this session.

## What the task framework does

Every task you work on has a lifecycle tracked in `proj_tasks.db`:

```
tasks__create    →  task is open
tasks__set_active →  task becomes wip; session_id bound in checkpoint
  (each UPS turn)   →  task history injected into your system prompt automatically
task:<id> done   →  auto-closes task at session stop (keyword detection)
tasks__finish    →  explicit close with reason
```

The active task's turn history (last 10 turns: summary + tools used) is injected into your `## Task history (this session)` system prompt block automatically — you don't need to ask for it.

## Getting the session_id

The session_id is always in your `## Turn state` system prompt block:

```
## Turn state
- session_id: <uuid>
- prompt_id: <uuid>
```

Or fetch it via MCP: `mcp__local-mac__session__current()`

## Steps when invoked with a task description

### 1. Create the task

```python
mcp__local-mac__tasks__create(title="<short title>", body="<context / plan>")
# returns: {"id": "<task_id>", ...}
```

### 2. Activate it for this session

```python
mcp__local-mac__tasks__set_active(task_id="<task_id>", session_id="<session_id from Turn state>")
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

- Use `tasks__list()` to see all open/wip tasks
- Use `tasks__history(id)` to inspect logged turn events
- Use `tasks__update(id, body=...)` to append notes mid-task

### 5. Closing the task

**Preferred — say it in your message to the user:**
```
task:<id> done
```
The stop hook detects this and auto-closes + clears the checkpoint.

**Explicit — call the finish tool:**
```python
mcp__local-mac__tasks__finish(task_id="<id>", session_id="<session_id>", reason="<what was accomplished>")
```

**Manual fallback:**
```python
mcp__local-mac__tasks__update(id="<id>", status="done")
```
Then clear the checkpoint:
```bash
uv run python scripts/task_activate.py clear <session_id>
```

## Steps when invoked without a task description

List open tasks and ask the user which to work on:

```python
mcp__local-mac__tasks__list()
```

Display them and ask: "Which task do you want to activate, or shall I create a new one?"

## Rules

- **One active task per session.** If `tasks__set_active` returns an error about an existing active task, call `tasks__clear_active` first (or use the script).
- **Never guess the session_id.** Always read it from `## Turn state` or `session__current()`.
- **task_memories are scored automatically** on activation — you don't need to load them manually.
- **task_context is session-scoped** — only turns from the current session appear in `## Task history`.
- Mark tasks `done` promptly. Stale `wip` tasks accumulate stale memories.
