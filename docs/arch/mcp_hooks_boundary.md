---
tags: MCP boundary, hooks boundary, MCP tools, hook server, read-only access, permitted access, session state, checkpoint, tool routing, dispatcher, MCP vs hooks, architecture boundary, FastAPI, uvicorn
---
# MCP / Hooks Boundary

This document defines the ownership boundary between the MCP tool layer and the LangGraph hooks layer. Violating this boundary makes each layer harder to test and reason about independently.

---

## The Rule

```
MCP tools   →  own domain DBs only (proj_tasks.db, MEMORY.sqlite, claude_hooks.sqlite)
               stateless API — write data, return immediately
               NEVER write to langgraph_checkpoints.db

Hooks       →  own session state (langgraph_checkpoints.db)
               react to MCP tool calls via PostToolUse
               the bridge between MCP writes and checkpoint updates
```

---

## Why this matters

MCP tools run in a separate process from hooks. They don't have access to the LangGraph runtime, so any checkpoint writes must shell out to `scripts/task_activate.py` (a subprocess with a cold start). This is:

- Slow (uv cold start per call)
- Brittle (process serialization, JSON round-trip)
- A boundary violation — the MCP layer is doing hook work

The clean model: MCP tools are a **stateless API** (write to domain DB, return). The PostToolUse hook fires automatically after every tool call — it's the right place to react to tool calls and update session state.

---

## PostToolUse bridge nodes

`session_graph.py` PostToolUse chain routes to one of these nodes based on `tool_name`:

| Tool | Node | What it does |
|------|------|--------------|
| `tasks__set_active` | `ActivateTaskNode` | Reads `task_id` from `tool_input`; looks up task in `proj_tasks.db` and scores memories inline; writes `active_task_id`, `active_task_title`, `task_memories`, `task_stack` to checkpoint |
| `tasks__pop_active` | `ActivateTaskNode` | Pops `task_stack`; re-activates the previous task (same node handles both cases) |
| `tasks__clear_active` | `DeactivateTaskNode` | Zeros `active_task_id`, `task_stack`, `task_memories`, `mid_task_decisions` in checkpoint |
| `tasks__finish` | `DeactivateTaskNode` | Same as clear — task already marked done in DB by MCP tool |
| `tasks__add_decision` | `DecisionTaskNode` | Appends `decision` text to `mid_task_decisions` in checkpoint |
| all other tools | — | No checkpoint change; chain ends after `log_tool_usage` |

Graph topology (PostToolUse chain):

```
log_tool_usage → (conditional) → activate_task   → END
                              → deactivate_task → END
                              → decision_task   → END
                              → END
```

> `update_tool_keywords` was merged into `log_tool_usage` and removed as a standalone node.

---

## Activation state ownership

Three different stores, three different scopes:

| Store | What it owns | Lifetime |
|-------|-------------|---------|
| `proj_tasks.db` | Task data, `status=wip` | Permanent — survives session |
| `langgraph_checkpoints.db` | `active_task_id` for this session | Session-scoped — ephemeral |
| PostToolUse payload | The activation event itself | In-flight — no persistence |

`proj_tasks.db` knows a task was activated (`status=wip`). It does not know which session has it active right now — that's the checkpoint's job. This is intentional: the same task could be activated in a different session after a crash without leaving stale checkpoint state.

---

## Permitted read-only access

`hooks__checkpoint_query` (MCP tool in `src/tools/hooks.py`) reads `langgraph_checkpoints.db` but never writes. This is acceptable — it's an observability/debug tool.

---

## Known remaining uses of `_run_task_script`

`scripts/task_activate.py` is still the entry point for the standalone CLI fallback:

```bash
uv run python scripts/task_activate.py activate <task_id> <session_id>
uv run python scripts/task_activate.py clear <session_id>
uv run python scripts/task_activate.py pop <session_id>
```

These are useful for manual recovery and testing. They bypass the PostToolUse path and write directly to the task_graph checkpoint. This is acceptable for manual use — not for production MCP calls.

---

← [Architecture](../ARCHITECTURE.md) · [Task Framework](task_framework.md) · [Graph & Pipeline](graph_pipeline.md)
