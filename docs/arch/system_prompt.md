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
- task:<id> — <title> [done]
```

Top-3 completed tasks scored by BM25 overlap against the active task title and body. Written by `load_related_tasks`. Useful for surfacing prior art.

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
