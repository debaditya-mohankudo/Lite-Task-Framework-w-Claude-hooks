---
tags: task framework, tasks, task lifecycle, task activation, task history, per-turn context injection, session_graph, UserPromptSubmit, tasks__create, tasks__set_active, tasks__finish, active task, task events, task_events, proj_tasks.db, parent task, subtask, epic, task stack, context switch, task memories, execution contract, task-grooming, task-implementation, task-introspection
---
# Task Framework Architecture

The task framework gives Claude persistent, session-aware awareness of the work it is doing. A task is the unit of work; the framework tracks when it starts, what happens each turn, and what tools are used — building a feedback loop that surfaces relevant context automatically.

**The framework is entirely skill-driven.** There is no separate "task engine" you configure — `/task-grooming`, `/task-implementation`, and `/task-introspection` are markdown-prose skills that Claude reads and follows, calling the same `tasks__*` MCP tools any user could call directly. The LangGraph/checkpoint machinery below exists to make context injection automatic (so Claude doesn't have to ask for task history every turn) — it does not enforce *how* you work a task. That's the skills' job.

---

## The three-phase lifecycle

```text
create → activate → groom → implement → finish → introspect
                      ↓          ↓                    ↓
               /task-grooming  /task-implementation  /task-introspection
```

| Phase | Skill | What it does |
| --- | --- | --- |
| Groom | `/task-grooming` | Pre-implementation pass — activates the task to pull related-task/commit/memory context, audits the body for gaps (missing file paths, deferred decisions, duplicate ownership with sibling tasks), then resets to `open`. Reduces uncertainty *before* work starts. |
| Implement | `/task-implementation` | Behavioral guide for the actual work — understand → think → implement → validate → reflect, plus explicit warning signs for drift (repeated searches without action, expanding scope, debugging without a hypothesis). Not a separate command — it's how to think while a task is active. |
| Introspect | `/task-introspection` | Post-close retrospective — captures unlogged decisions, flags stale memories/concepts, and asks "what would make the next execution better" rather than just summarizing what happened. |

None of these are mechanically enforced by a gate — they're skills you (or Claude) choose to invoke. The one piece of this that *is* mechanically enforced every turn is the **Execution Contract** (below).

---

## Task lifecycle (DB + checkpoint)

```text
tasks__create      →  task is open in proj_tasks.db (no active session yet)
tasks__set_active  →  active_task_id bound in the LangGraph checkpoint (NOT a DB status)
  (each UPS turn)  →  task context injected into system prompt automatically
tasks__pop_active  →  restore a previously suspended task from task_stack
tasks__finish      →  status → done in proj_tasks.db; checkpoint cleared; retrospective prompt injected
```

**Important:** `active` is a checkpoint concept, not a database status. `open_tasks.status` only ever holds `open`, `done`, `abandoned`, or `blocked` — valid transitions are `open → {done, blocked}`, `blocked → open`, and any status `→ abandoned`. `handle_update()` in `src/tools/tasks.py` is the only path that changes status; it always calls `is_valid_transition()`. Never run `UPDATE open_tasks SET status=...` directly — see [Gates](gates.md)'s `TaskUpdateGate`/`TaskFinishGate`.

There used to be a review-gate stage (`review` status, `review_runs`, a blocking `TaskDoneGate`) — it was removed. Retrospection is handled by `/task-introspection` on demand, not a blocking checklist before `done`.

---

## Execution Contract — the one thing that's automatic every turn

At `tasks__set_active`, a fixed north-star string is written into the checkpoint and re-rendered in the system prompt **byte-identical, every turn**, for as long as the task stays active:

```text
You are executing task:<id> — <title>.

Every action should move this task toward completion. Do not optimize for
exploration; optimize for finishing the current objective.

Before using a tool, ask yourself:
- Does this reduce uncertainty?
- Does this directly advance implementation?
- Am I repeating work?
- Is there a smaller next step?

1. Keep the task objective in focus.
2. Prefer the smallest action that increases confidence or delivers progress.
3. Search only until you can act.
4. Validate assumptions before building on them.
5. Replan when evidence changes.
6. Detect repeated work and change strategy.
7. Capture durable knowledge when discovered.
8. Finish decisively rather than optimizing endlessly.

See /task-implementation for the full execution loop and warning signs.
```

This is deliberately the **compressed, pinned** counterpart to `/task-implementation`'s expanded philosophy, not a duplicate to maintain independently — it exists because checkpoint injection survives context compaction on long tasks, which a skill invoked once at the start cannot guarantee. Built by `_build_execution_contract()` in `langchain_learning/nodes/activate_task.py`. Exempt from both `_enforce_context_budget` (memory eviction) and `_TASK_BODY_CHAR_CAP` (task body truncation) by design — see [System Prompt](system_prompt.md).

---

## What "tracking a turn" means

Every time you submit a prompt while a task is active, the Stop hook writes one row to `task_events`:

| Field | What it captures |
| --- | --- |
| `summary` | First 200 chars of your prompt text |
| `tools` | Comma-separated list of tools called (e.g. `Edit,Bash,Read`) |
| `turn` | Turn number within the session |
| `session_id` | Which Claude Code session this happened in |

Lightweight, one row per prompt. When you resume a task in a new session, Claude reads this log and injects it as `## Task history`.

---

## One graph, PostToolUse bridge

Everything routes through a single `StateGraph` (`langchain_learning/session_graph.py`) handling all four hook events — there is no separate task-specific graph.

```text
UserPromptSubmit chain (when a task is active):
  load_turn → load_active_task
      → [load_task_history ∥ load_task_code ∥ load_related_tasks ∥ load_related_commits]  (parallel)
      → summarize_task_context  (fan-in; compresses the above, first-turn-of-activation gated)
      → [cwd_domain_detect ∥ load_memories ∥ score_tools]  (parallel)
      → set_prompt_id → log_task_events → END

PostToolUse chain (task lifecycle tools):
  log_tool_usage
    → tasks__set_active / tasks__pop_active   → ActivateTaskNode → (task_files?) → BackfillMemoryFilesNode
    → tasks__clear_active / tasks__finish     → DeactivateTaskNode (clears checkpoint; injects retrospective prompt on finish)
    → tasks__add_decision                     → DecisionTaskNode (appends to mid_task_decisions)
```

`ActivateTaskNode` writes `active_task_id`, `task_memories`, `task_files`, `active_task_domain`, and `execution_contract` into the checkpoint — this is the only place any of those fields get set. MCP tools never write to the checkpoint directly; they write to `proj_tasks.db` and the PostToolUse bridge node reacts. See [MCP / Hooks Boundary](mcp_hooks_boundary.md).

---

## Mid-task decision tracking

Explicit design decisions logged during an active task persist in the checkpoint and are injected into every subsequent turn's system prompt as `## Task decisions` — so a load-bearing choice ("chose opaque tokens over JWT — avoids key rotation complexity") doesn't vanish from context after a few turns or a compaction.

**Flow:**

1. `/log-decision` (skill folder: `skills/task-log-decision/`), or saying "log this decision: chose X over Y because Z"
2. The skill calls `tasks__add_decision(task_id, decision, session_id)` (`src/tools/tasks.py::handle_add_decision`, in claude-hooks — not `claude_for_mac_local`)
3. PostToolUse routes to `DecisionTaskNode` (`langchain_learning/nodes/decision_task.py`), which appends the decision to `mid_task_decisions` in the checkpoint (capped to the most recent 15 — see `_MAX_DECISIONS`)
4. The durable record is written to `task_events` with `tools='decision'` — permanent, queryable via `tasks__history(id)` regardless of checkpoint state

**Cross-session note:** `mid_task_decisions` is checkpoint-only — it is cleared on deactivation and is **not** currently auto-restored from `task_events` when a task is re-activated in a new session. The full decision history is never lost (it's in `task_events`), but it won't automatically reappear in the injected `## Task decisions` block until something reads it back in — call `tasks__history(id)` explicitly if resuming a task in a fresh session and you need prior decisions in context.

Decisions are only logged when **explicitly requested** — no auto-detection from response text. Every entry in `## Task decisions` is something deliberately chosen to preserve.

---

## Task stack (context-switch support)

`SessionState` carries `task_stack: list[str]` alongside `active_task_id`, enabling lossless context switching within a session.

- **Push (implicit, on `set_active`)** — if a task is already active when `tasks__set_active` is called, it's pushed onto `task_stack` before the new one is written.
- **Pop (`tasks__pop_active`)** — restores the most recently suspended task from the stack; clears active task if the stack is empty.
- **Clear (`tasks__clear_active`)** — zeros both `active_task_id` and `task_stack`.

---

## Subtasks and parent tracking

```python
parent = tasks__create(title="Portfolio DB — implement JSON storage", issue_type="epic", cwd="...")
sub1   = tasks__create(title="Select DB format",   parent_id=parent["id"], issue_type="story", cwd="...")
sub2   = tasks__create(title="Migrate schema",      parent_id=parent["id"], issue_type="story", cwd="...")
```

`parent_id` tags the child `parent:<id>` and the `JiraHierarchyGate` enforces valid nesting (`story`/`task` need an `epic` parent, `subtask` needs a `bug`/`story`/`task` parent, `epic` needs none — see [Gates](gates.md)). `tasks__list()` groups children under their parent; the parent auto-closes once every child is `done`. A single, standalone piece of work with no parent must use `issue_type='epic'` — there's no "small standalone task" type.

---

## Getting the session_id

Read it from the `## Turn state` system prompt block, injected every turn:

```text
## Turn state
- session_id: <uuid>
- prompt_id: <uuid>
```

If it isn't visible (e.g. a cold script context), use `hooks__session_id` — it hits the same live checkpoint with a built-in retry for the brand-new-session race. Never guess it.

---

## MCP tools

| Tool | Effect |
| --- | --- |
| `tasks__create(title, body?, task_type?, issue_type?, parent_id?, cwd?)` | Insert row into `open_tasks`; auto-fills body from a template if omitted |
| `tasks__set_active(task_id, session_id)` | PostToolUse `ActivateTaskNode` writes checkpoint (active_task_id, memories, execution_contract) |
| `tasks__clear_active(session_id)` | `DeactivateTaskNode` zeros checkpoint fields |
| `tasks__pop_active(session_id)` | `ActivateTaskNode` pops `task_stack`, re-activates previous task |
| `tasks__finish(task_id, session_id, reason?)` | Status → `done`; checkpoint cleared; retrospective prompt injected |
| `tasks__update(id, status?, body?, issue_type?, tags?)` | Update fields; status changes gated by `is_valid_transition()` |
| `tasks__add_decision(task_id, decision, session_id)` | Appends to `mid_task_decisions` (capped to last 15) |
| `tasks__list(status?, limit?)` | Open/done tasks grouped by parent |
| `tasks__history(id)` | All `task_events` rows for a task |
| `tasks__link_tasks(from_id, to_id, relation_type?)` | Directed edge in `task_edges` — cross-graph relations distinct from `parent_id` hierarchy |
| `tasks__neighbors(task_id)` | Top-5 semantically similar tasks via TurboVec |

---

## Database schema

See [Databases](databases.md) for the full inventory. Task-relevant tables:

- `proj_tasks.db` → `open_tasks` (id, title, body, status, issue_type, parent_id, tags) + `task_events` (task_id, prompt_id, session_id, turn, summary, tools) + `task_edges` (from_id, to_id, relation_type)
- `langgraph_checkpoints.db` → checkpoint fields include `active_task_id`, `active_task_title`, `task_memories`, `task_stack`, `mid_task_decisions`, `execution_contract`

---

← [Architecture](../ARCHITECTURE.md) · [Gates](gates.md) · [System Prompt](system_prompt.md) · [Databases](databases.md)
