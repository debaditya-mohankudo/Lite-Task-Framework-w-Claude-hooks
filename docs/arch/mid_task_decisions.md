---
tags: mid-task decisions, decision tracking, task-log-decision skill, tasks__add_decision, decision log, task decisions, mid_task_decisions, /task-task-log-decision, decision history, architectural decisions, implementation decisions
---
# Mid-Task Decision Tracking

Explicit design decisions logged during an active task are persisted in the checkpoint and injected into every subsequent turn's system prompt.

---

## Problem

Key design choices made mid-task (e.g. "chose opaque tokens over JWT — avoids key rotation complexity") live only in conversation history. After 5 turns they vanish from the injected context, causing goal drift on long tasks.

## Solution

`mid_task_decisions: list[str]` in `SessionState` — persisted in the LangGraph checkpoint, backed by `task_events`, injected every turn while the task is active.

---

## Flow

1. Mid-task, the user says `/task-task-log-decision` or "log this decision: chose X over Y because Z"
2. The `/task-task-log-decision` skill calls `tasks__add_decision(task_id, decision, session_id)`
3. Decision is written to `task_events` (permanent, survives restarts) with `tools='decision'`, and appended to `mid_task_decisions` in the LangGraph checkpoint via `run_add_decision`
4. Next turn the dispatcher renders `## Task decisions` in the system prompt — the model always sees all load-bearing choices for the active task

---

## Session boundary handling

On re-activation (`tasks__set_active`), `_load_decisions_from_db` queries all `task_events` rows with `tools='decision'` for the task and restores them into `mid_task_decisions` in the checkpoint. Nothing is lost between sessions.

---

## Files

| File | Role |
| --- | --- |
| `langchain_learning/session_state.py` | `mid_task_decisions: list[str]` field |
| `langchain_learning/task_graph.py` | `run_add_decision` (checkpoint patch), `_load_decisions_from_db` (session restore), clear on pop/close |
| `langchain_learning/nodes/log_task_events.py` | Clears `mid_task_decisions` on auto-close |
| `hooks/dispatcher.py` | Renders `## Task decisions` section |
| `scripts/task_activate.py` | `decision` CLI command — shells out to `run_add_decision` |
| `claude_for_mac_local/src/tools/tasks.py` | `handle_add_decision` MCP tool |
| `claude_for_mac_local/src/dispatcher.py` | Registers `tasks__add_decision` |
| `skills/task-task-log-decision/skill.md` | User-invocable skill — composes and logs the decision |

---

## Storage

Decisions are stored in `task_events` alongside turn events:

```sql
INSERT INTO task_events (task_id, session_id, summary, tools)
VALUES (?, ?, '<decision text>', 'decision')
```

The `tools='decision'` marker distinguishes them from normal turn events. `_load_decisions_from_db` filters on this to restore on re-activation.

---

## Design choice

Decisions are only logged when **explicitly requested** — no auto-detection from response text. This keeps the signal high-quality: every entry in `## Task decisions` is something the user deliberately chose to preserve.

---

← [Architecture](../ARCHITECTURE.md) · [Task Framework](task_framework.md) · [Design Decisions](design_decisions.md)
