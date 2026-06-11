# claude-hooks

A personal memory and session layer for Claude Code — making conversations feel continuous and context-aware.

Every prompt submission runs a LangGraph pipeline that scores memories, retrieves relevant session summaries, loads active task history, and injects all of it into Claude's system context before it responds. Claude never starts cold.

## What it does

| Layer | Description |
| --- | --- |
| **Memory** | Persistent facts (user, feedback, project, reference) scored and injected per-prompt |
| **Sessions** | Conversations tracked and summarized; retrievable via MCP for prior context |
| **Tool hints** | BM25-scored tool suggestions surfaced based on prompt domain |
| **Task tracking** | Multi-session tasks with turn history, auto-injected when active |
| **Gates** | Pre-tool-use rules that block or validate specific tool calls |

## Example: working on a task across sessions

You start a bug fix and create a task so the work is tracked:

```text
You: fix the auth token expiry bug  /task-framework

Claude: Creates task:a3f1b2 — "Fix auth token expiry"
        Activates it for this session.
        Task a3f1b2 active. Say "task:a3f1b2 done" when finished.
```

Claude reads relevant memories and prior context before touching any code. You work through a few prompts. Claude commits mid-way:

```text
You: /gc

Claude: Runs tests → passes
        Committed: "fix(auth): correct token expiry check  task:a3f1b2"
```

Next day, new session — you pick up where you left off:

```text
You: task:a3f1b2

## Task history (this session) is auto-injected:
- turn 4: traced expiry to missing UTC offset in validate_token() [Read, Bash]
- turn 6: added test for timezone-naive token edge case [Edit]

Claude: Resumes with full context of what was done yesterday.
```

When finished:

```text
You: task:a3f1b2 done

Claude: Marks done, clears checkpoint, logs final turn.
```

The full turn-by-turn history is preserved in `proj_tasks.db` — searchable, linkable to commits.

---

## Docs

- [Architecture](docs/ARCHITECTURE.md) — pipeline overview, node graph, data flow
- [Setup](docs/setup.md) — installation and configuration
- [Skills](docs/skills.md) — task-framework, task-create, log-decision skills
- [Task framework](docs/arch/task_framework.md) — task lifecycle, subtasks, commit flow

## MCP server

All memory, session, and task data is also exposed as MCP tools — Claude can read, write, and search mid-conversation.

```bash
uv run python mcp_server.py  # stdio transport
```
