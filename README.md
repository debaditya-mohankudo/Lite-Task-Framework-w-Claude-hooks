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
