# Task Framework Architecture

The task framework provides persistent, session-spanning work tracking via `proj_tasks.db` (SQLite). Tasks are created, activated, and closed through MCP tools (`tasks__*`) hosted in the `local-mac` server.

For usage docs see [task_framework.md](../task_framework.md).

---

## Task Database

| File | Purpose |
|------|---------|
| `~/.claude/proj_tasks.db` | Task rows (`open_tasks`) + turn event log (`task_events`) |

`open_tasks` holds the task title, body, tags, and status (`open` / `wip` / `done`). `task_events` is an append-only log of turn summaries, tools used, and prompt IDs — one row per turn while a task is active.

---

## Task Lifecycle

```text
tasks__create     →  status: open  (stored in proj_tasks.db)
tasks__set_active →  status: wip; active_task_id written to LangGraph checkpoint
  (each UPS turn) →  load_active_task reads checkpoint; injects task_memories + task_context
"task:<id> done"  →  log_task_events detects keyword; flips status=done; clears checkpoint
tasks__finish     →  explicit close with reason; same checkpoint clear
```

---

## Task Activation (`task_graph`)

`tasks__set_active` runs the **task graph** (`langchain_learning/task_graph.py`) — a separate, minimal graph:

```text
START → set_active_task → load_task_memories → END
```

`set_active_task` writes `active_task_id` and `active_task_title` into the LangGraph checkpoint for that `session_id`. `load_task_memories` scores `MEMORY.sqlite` against task title+body keywords and writes `task_memories` into the same checkpoint. The task graph exits; the checkpoint now carries the task context.

If a task was already active, the current `active_task_id` is pushed onto `task_stack` before the new task is written — enabling lossless context switching within a session.

---

## Task Context Injection (UPS turns)

Once a task is activated, every `UserPromptSubmit` turn picks it up via four dedicated nodes:

```text
load_active_task   — reads active_task_id + task_memories from checkpoint
load_task_history  — reads task_events for (task_id, session_id), oldest-first
load_task_code     — TurboVec semantic search over .code_embeddings.tvim, top-3 symbols
load_related_tasks — BM25 overlap against done tasks, top-3 by title+tags+body score
```

These populate three complementary signals in `SessionState`, rendered by `dispatcher.py`:

| Field | Signal | What it answers |
| --- | --- | --- |
| `task_rag_chunks` | Current state | What code is relevant right now (symbols, files, line numbers) |
| `task_memories` | Long-term drivers | What principles and constraints apply (goals, arch decisions, feedback) |
| `task_context` | Recency | What happened in recent turns (summaries, tools used) |

---

## Task Auto-Close

`log_task_events` (the last UPS node before END) scans the outgoing response text for completion signals:

- **Primary:** `task:<id> done` — explicit; matched by regex `\btask:[a-f0-9]{6,}\s+done\b`
- **Fallback:** generic phrases (`marked as done`, `completed`, `finished`, `fixed`) within 40 chars

On match: flips `open_tasks.status = 'done'`, logs final event, clears `active_task_id` from checkpoint.

---

## Subtasks and Parent Auto-Close

`tasks__create(parent_id=<id>)` appends a `parent:<id>` tag to the subtask. When `tasks__finish` marks the last subtask done, it queries all siblings and auto-closes the parent if all are done.

---

## task_graph vs session_graph

| Aspect | `task_graph.py` | `session_graph.py` |
|--------|-----------------|-------------------|
| Triggered by | `tasks__set_active` MCP call | Every hook event |
| Purpose | Activation one-shot: write task + memories to checkpoint | Main pipeline: inject context, score memories, gate tools |
| Nodes | `set_active_task`, `load_task_memories` | Full pipeline (10+ nodes) |
| Reads task from | MCP arg | LangGraph checkpoint |
