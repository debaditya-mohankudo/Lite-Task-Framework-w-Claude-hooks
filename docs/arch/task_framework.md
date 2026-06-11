# Task Framework Architecture

The task framework gives Claude persistent, session-aware awareness of the work it is doing. A task is the unit of work; the framework tracks when it starts, what happens each turn, and what tools are used — building a feedback loop that surfaces relevant context automatically.

For usage docs see [task_framework.md](../task_framework.md).

---

## Rules

- **Create and activate a task before any code change.** Call `tasks__create` then `tasks__set_active` before the first Edit/Write/Bash call. No exceptions — even for one-liners.
- **One active task per session.** If a task is already active, call `tasks__clear_active` (or `tasks__pop_active` if you want to restore it later) before activating a new one.
- **Work tasks sequentially.** Complete and close one task before activating the next — don't parallelize unless tasks are fully independent with no shared state.
- **Never guess the session_id.** Read it from `## Turn state` or call `session__current()`.
- **Mark tasks done promptly.** Stale `wip` tasks accumulate stale memories.
- **Reindex after edits.** After editing source files, call `code_rag__index_files` so semantic search stays current.

---

## Core Concepts

### Task lifecycle

```text
tasks__create      →  task is open (no active session yet)
tasks__set_active  →  task becomes wip; session_id + task_id bound in checkpoint
                       if a task was already active, it is pushed onto task_stack
  (each UPS turn)  →  task context injected into system prompt
tasks__pop_active  →  restore previously suspended task from stack
task:<id> done     →  auto-closes task at stop (keyword detection)
tasks__finish      →  explicit close with reason
```

### Two graphs, one shared checkpoint

The framework spans two LangGraph graphs that share the same SqliteSaver checkpoint DB (`~/.claude/langgraph_checkpoints.db`), keyed by `session_id`:

| Graph | Triggered by | Responsibility |
| --- | --- | --- |
| `task_graph.py` | `tasks__set_active` MCP call | Activate task, score task memories, write checkpoint |
| `session_graph.py` | Every Claude Code hook event | Read checkpoint, inject context, log events at stop |

Because they share the checkpoint, state written by `task_graph` (e.g. `active_task_id`, `task_memories`, `task_stack`) is immediately visible to the next `session_graph` invocation for the same session — no explicit handshake needed.

---

## Subtasks and parent tracking

When decomposing a task into subtasks, use the `parent_id` parameter of `tasks__create` to link them:

1. Create the parent task first (a short umbrella title)
2. Create each subtask with `parent_id=<parent_task_id>` — this appends `parent:<id>` to the tags column
3. `tasks__list` groups subtasks under their parent in output
4. When all subtasks are `done`, the parent is auto-closed

```python
parent = tasks__create(title="Portfolio DB — implement JSON storage", cwd="...")
sub1   = tasks__create(title="Select DB format",   parent_id=parent["id"], cwd="...")
sub2   = tasks__create(title="Migrate schema",      parent_id=parent["id"], cwd="...")
sub3   = tasks__create(title="Wire up tools",       parent_id=parent["id"], cwd="...")
# activate sub1 first; work sequentially
```

> **Tip:** The parent task is never activated directly — only subtasks are activated and worked on. The parent closes automatically once all its subtasks are done.

---

## Activation flow (`task_graph`)

```text
tasks__set_active(task_id, session_id)
        │
        ▼
  run_task_activate()
        │
   [if active_task_id already set] → push current onto task_stack
        │
        ▼
  START → set_active_task → load_task_memories → END
```

### `set_active_task` node

- Looks up `task_id` in `proj_tasks.db → open_tasks`
- Writes `active_task_id` + `active_task_title` + `task_stack` into state
- Flips status `open → wip` in the DB

### `load_task_memories` node

- Tokenises `active_task_title` with `tokenise()` (from `_text_utils.py`)
- Scores all rows in `MEMORY.sqlite` against task tokens (title + tags overlap)
- Priority-1 memories always included regardless of score
- Top-10 stored as `task_memories` in state → checkpoint

---

## Task stack (context-switch support)

`SessionState` carries a `task_stack: list[str]` field alongside `active_task_id`. This enables lossless context switching within a session.

### Push (implicit — on `set_active`)

When `tasks__set_active` is called and a task is already active, the current `active_task_id` is appended to `task_stack` before the new task is written. No data is lost.

### Pop (`tasks__pop_active`)

Restores the most recently suspended task:

1. Pops the last entry from `task_stack`
2. Re-activates it via the `task_graph` (fresh memory scoring)
3. If stack is empty → clears active task instead

### Clear

`tasks__clear_active` zeros both `active_task_id` and `task_stack`.

---

## Per-turn context injection (`session_graph`, UserPromptSubmit)

Every prompt goes through:

```text
load_turn → load_active_task → load_task_context → load_memories → ...
```

### `load_active_task` node

- Pure pass-through — `active_task_id` already lives in the checkpoint
- Logs presence for observability; no DB lookup

### `load_task_context` node — hybrid scope

Uses a hybrid strategy to balance session relevance vs. cross-session continuity:

| Condition | Behaviour |
| --- | --- |
| Current session has ≥ 5 turns for this task | All current-session events (no limit) |
| Current session has < 5 turns | Last 5 events across all sessions |

This means: if you're deep in a session, context stays tightly scoped. If you've just resumed a task in a new session, prior history fills the gap automatically.

Returns per-turn snapshots: `{turn, summary, tools, session_id}`

### System prompt injection

The following sections are added to `additionalSystemPrompt` when a task is active:

```text
## Active task: task:<id> — <title>

## Task memories
### <memory-name> [<domain>]
<body>

## Task history (this session)
- turn 3: user asked about gate architecture [Bash,Read]
- turn 5: fixed type error in task_graph.py [Edit]
```

---

## Event logging at session stop (`session_graph`, Stop)

```text
Stop hook → log_task_events → END
```

### `log_task_events` node

- No-ops if no active task
- Reads `current_prompt_text.tmp` for a 200-char prompt summary (then deletes the tmp file)
- Inserts one row into `task_events`
- `tools` is a comma-joined list of tool short-names called during the prompt (from `prompt_tools` in state)
- Also bumps `open_tasks.updated_at`

### Auto-completion detection

If the prompt text contains completion signals, the task is auto-closed:

- **Preferred convention:** `task:<id> done` — unambiguous, detected by `_TASK_DONE_PATTERN`
- **Heuristics:** `completed the X`, `finished X`, `all tests passing`, `task complete`, `it works!`
- On match: flips `status = 'done'`, clears checkpoint, zeroes state

### Explicit close

```python
mcp__claude-hooks__tasks__finish(task_id, session_id, reason?)
```

---

## Database schema

### `proj_tasks.db`

```sql
open_tasks (
  id         TEXT PRIMARY KEY,   -- short UUID
  title      TEXT,
  body       TEXT,
  status     TEXT,               -- open | wip | done
  tags       TEXT,               -- includes parent:<id> for subtasks
  created_at TIMESTAMP,
  updated_at TIMESTAMP
)

task_events (
  id         INTEGER PRIMARY KEY,
  task_id    TEXT,               -- FK → open_tasks.id
  prompt_id  TEXT,               -- UUID from set_prompt_id node
  session_id TEXT,               -- Claude Code session UUID
  turn       INTEGER,
  summary    TEXT,               -- first 200 chars of prompt text
  tools      TEXT                -- comma-separated tool names
)
```

### `langgraph_checkpoints.db` — task-relevant fields

| Field | Type | Notes |
| --- | --- | --- |
| `active_task_id` | str | Currently active task; empty when none |
| `active_task_title` | str | Title of active task |
| `task_memories` | list[dict] | Memories scored at activation |
| `task_stack` | list[str] | LIFO stack of suspended task IDs |

---

## Getting the session_id

`session_id` is injected by Claude Code into every hook event payload.

To get it programmatically:

```python
mcp__claude-hooks__session__current()
# → {"session_id": "<uuid>"}
```

It is also always in the `## Turn state` system prompt block:

```text
## Turn state
- session_id: <uuid>
- prompt_id: <uuid>
```

---

## MCP tools (via `claude-hooks`)

| Tool | Effect |
| --- | --- |
| `tasks__create(title, body?, parent_id?, cwd?)` | Insert row into `open_tasks`; `parent_id` tags as `parent:<id>`; `cwd` auto-tags `project:<name>` |
| `tasks__set_active(task_id, session_id)` | Run `task_graph` → activate + score memories; auto-pushes current task onto stack if one is active |
| `tasks__clear_active(session_id)` | Zero `active_task_id` and `task_stack` in checkpoint |
| `tasks__pop_active(session_id)` | Restore previously suspended task from stack; clears if stack empty |
| `tasks__finish(task_id, session_id, reason?)` | Mark done + log final event + clear checkpoint |
| `tasks__update(id, status?, body?)` | Update status / body |
| `tasks__list(status?, limit?)` | All open/wip tasks grouped by parent; default limit 50 |
| `tasks__history(id)` | All `task_events` rows for a task |

## Code search tools (via `claude-hooks`)

Use these to locate code while working on a task. They operate against a pre-built embedding index (`.code_embeddings.tvim`) in the repo root.

| Tool | When to use |
| --- | --- |
| `code_rag__smart_search(query, repo?, k?)` | **First choice.** Hybrid FTS over code graph + TurboVec semantic rerank. Best for symbol names and natural-language concepts. |
| `code_rag__query(query, repo?, k?)` | Pure semantic search. Use as fallback when `smart_search` returns no useful hits. |
| `code_rag__index_files(files, repo?)` | Incremental reindex after editing files. Keep the index current so searches reflect your changes. |

`repo` defaults to `claude-hooks` if omitted — pass an absolute path when working in another project.

**Workflow pattern:**

```python
# 1. Find relevant code before reading files
mcp__claude-hooks__code_rag__smart_search(query="load_task_context node", repo="/Users/you/workspace/myrepo")

# 2. After editing, reindex the changed files
mcp__claude-hooks__code_rag__index_files(files=["src/nodes/load_task_context.py"], repo="/Users/you/workspace/myrepo")
```

## Fallback (if MCP env missing langgraph)

```bash
cd ~/workspace/claude-hooks
uv run python scripts/task_activate.py activate <task_id> <session_id>
uv run python scripts/task_activate.py clear <session_id>
uv run python scripts/task_activate.py pop <session_id>
```

---

## Related

- Skill: [task-framework](../../skills/task-framework/skill.md)
- Usage docs: [task_framework.md](../task_framework.md)
