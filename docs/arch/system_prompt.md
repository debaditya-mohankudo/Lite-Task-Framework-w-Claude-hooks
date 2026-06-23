---
tags: system prompt, additionalSystemPrompt, dispatcher, active task, task memories, task context, task history, relevant code, related past tasks, related commits, injected memories, suggested tools, turn state, prompt injection, task context summary, mid-task decisions, session state, UserPromptSubmit output
---
# System Prompt Injection

`dispatcher.py` assembles `SessionState` outputs into `additionalSystemPrompt` on every `UserPromptSubmit`. Sections are only included when the relevant data is non-empty.

---

## Active task

```text
## Active task
task:<id> — <title>
```

Present only when `active_task_id` is set in the checkpoint. The task ID and title are read from the checkpoint — no DB lookup per turn.

---

## Task memories

```text
## Task memories
### <memory-name> [<domain>]
<body>
```

Memories scored against the active task's title+body keywords at activation time (via `load_task_memories` in `task_graph.py`). Injected every turn while the task is active.

---

## Task context

```text
## Task context
• Fixed auth gate — moved contacts__search prereq check to window-based lookup
• Added load_related_commits node; wired into tier-1 fan-out
• Key files: hooks/gates.py, langchain_learning/nodes/load_related_commits.py
• Related prior work: gate_check refactor (task:abc123)
```

Present **instead of** the four raw blocks below (`## Task history`, `## Relevant code`, `## Related past tasks`, `## Related commits`) when `task_context_summary` is non-empty. Written by `SummarizeTaskContextNode` — invoked only when total raw context > 800 chars; falls back to raw blocks on timeout or error.

Saved to vault at `TaskContexts/<task-id>/<date>_<session[:8]>.md` (once per session per task, fire-and-forget).

---

## Task history (this session)

```text
## Task history (this session)
- turn 3: user asked about gate architecture [Bash,Read]
- turn 5: fixed type error in task_graph.py [Edit]
```

Written by `load_task_history`. Uses a hybrid scope:

| Condition | Behaviour |
| --- | --- |
| Current session has ≥ 5 turns for this task | All current-session events |
| Current session has < 5 turns | Last 5 events across all sessions |

**Suppressed when `task_context_summary` is present** — `## Task context` covers this.

---

## Relevant code

```text
## Relevant code
- `LoadTaskCodeNode` — langchain_learning/nodes/load_task_code.py:40
- `_query_tvim` — langchain_learning/nodes/load_task_code.py:21
```

Top-3 code symbols semantically closest to the active task title. Written by `load_task_code` using TurboVec vector search over `.code_embeddings.tvim` (Ollama nomic-embed-text embeddings). Falls back to empty if the index doesn't exist or Ollama is unavailable.

---

## Related past tasks

```text
## Related past tasks
- task:<id> — <title> (score: 0.87)
- task:<id> — <title> (score: 0.81)
```

Top-3 completed tasks by cosine similarity to the active task title + body. Written by `load_related_tasks` using TurboVec semantic search over `.tasks_embeddings.tvim` (Ollama `nomic-embed-text` embeddings). Falls back to empty if the index or Ollama is unavailable.

**Suppressed when `task_context_summary` is present.**

---

## Related commits

```text
## Related commits
- `a1b2c3d4` langchain_learning/nodes/gate_check.py [0.891]
- `f9e8d7c6` hooks/gates.py [0.874]
```

Top-3 diff hunks by cosine similarity to the active task title. Written by `load_related_commits` using TurboVec semantic search over `.diff_embeddings.tvim`. Built incrementally after each commit via `diff_rag__index_commits`. Falls back to empty if the index is missing or Ollama is unavailable.

Prefer `diff_rag__smart_search` over raw git commands — it searches this index directly.

**Suppressed when `task_context_summary` is present.**

---

## Task decisions

```text
## Task decisions
- Chose X over Y — avoids Z
```

Explicit design decisions logged via `/log-decision` during the active task. Persisted in `task_events` and restored on re-activation. See [Mid-Task Decisions](mid_task_decisions.md).

---

## Injected memories

```text
## Injected memories
### <memory-name> [<domain>]
<body>
```

Memories from `MEMORY.sqlite` scored against prompt keywords (BM25-style keyword overlap). Written by `load_memories`. Top-5 by score.

---

## Suggested tools

```text
## Suggested tools
- tool_name (domain): hint text
```

Top-5 MCP tool hints from `tool_hints.sqlite`, scored by domain match + keyword overlap.

---

## Turn state

```text
## Turn state
- session_id: <uuid>
- prompt_id: <uuid>
```

Always injected. Gives Claude direct access to `session_id` and `prompt_id` without a tool call — required by several MCP tools that take these as explicit arguments.

---

← [Architecture](../ARCHITECTURE.md) · [Graph & Pipeline](graph_pipeline.md) · [Task Framework](task_framework.md)
