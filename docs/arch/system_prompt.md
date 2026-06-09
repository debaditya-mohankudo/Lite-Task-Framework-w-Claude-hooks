# System Prompt Injection

`dispatcher.py` assembles `SessionState` outputs into `additionalSystemPrompt` on every `UserPromptSubmit`. Sections are only included when the relevant data is non-empty.

---

## Active task

```
## Active task
task:<id> — <title>
```

Present only when `active_task_id` is set in the checkpoint. The task ID and title are read from the checkpoint — no DB lookup per turn.

---

## Task memories

```
## Task memories
### <memory-name> [<domain>]
<body>
```

Memories scored against the active task's title+body keywords at activation time (via `load_task_memories` in `task_graph.py`). Injected every turn while the task is active.

---

## Task history (this session)

```
## Task history (this session)
- turn 3: user asked about gate architecture [Bash,Read]
- turn 5: fixed type error in task_graph.py [Edit]
```

Written by `load_task_history`. Uses a hybrid scope:

| Condition | Behaviour |
|---|---|
| Current session has ≥ 5 turns for this task | All current-session events |
| Current session has < 5 turns | Last 5 events across all sessions |

---

## Task commits

```
## Task commits
- abc1234 2026-06-09: fix gate check for imessage__send
```

Last 5 git commits whose message references the active task ID or title keywords. Written by `load_task_commits`.

---

## Injected memories

```
## Injected memories
### <memory-name> [<domain>]
<body>
```

Memories from `MEMORY.sqlite` scored against prompt keywords (BM25-style). Priority-1 memories always injected. Written by `load_memories`.

---

## Suggested tools

```
## Suggested tools
- tool_name (domain): hint text
```

Top-5 MCP tool hints from `tool_hints.sqlite`, scored by domain match + keyword overlap. Omitted when `skip_tools=True` (no domain detected).

---

## Turn state

```
## Turn state
- session_id: <uuid>
- prompt_id: <uuid>
```

Always injected. Gives Claude direct access to `session_id` and `prompt_id` without a tool call — required by several MCP tools that take these as explicit arguments.
