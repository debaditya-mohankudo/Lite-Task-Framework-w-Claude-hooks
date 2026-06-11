# claude-hooks

A task tracking layer with persistent memory for Claude Code — so every session picks up where the last one left off.

## Task framework

Here's the problem: Claude has no memory between sessions. You close the window, come back the next day, and you're starting over — re-explaining what you were building, what you already tried, what the tricky part was. For anything that takes more than one sitting, this gets old fast.

The task framework fixes it. You describe what you want to work on and invoke `/task-framework`. Claude assesses whether it needs to be broken into subtasks, creates the task (or subtasks) using `/task-create`, activates it for the session, and starts tracking — every turn, every tool call, every decision gets logged.

Next session, you just say "continue task:a3f1b2". Claude reads the full history and picks up exactly where you left off — what was done, what was decided, what's still open. No recap, no re-explaining.

If you commit with `/gc` while the task is active, the task ID gets appended to the commit body automatically — so the git history ties back to the work log.

Here's one from this repo — moving all MCP tools into a self-contained server:

```text
migrate claude-hooks MCP tools into a standalone server  /task-framework
```

→ Claude proposes 3 subtasks: stand up FastMCP server, migrate task tools, cutover config  
→ `/task-create` creates parent `task:be7d66a5` + 3 subtasks, first subtask activated

```text
stand up mcp_server.py, wire memory + session tools  /gc
```

→ committed, tagged `task:4c1c7ab0` — next subtask activated, prior work already in context

```text
migrate tasks__* tools, add task_edges schema  /gc
```

→ committed — final subtask: update `~/.claude.json`, smoke-test, remove old dispatcher entries

```text
task:03af8768 done
```

→ parent auto-closes, context cleared

Three sessions, three subtasks, zero re-explaining.

---

## Docs

- [Architecture](docs/ARCHITECTURE.md) — pipeline overview, node graph, data flow
- [Setup](docs/setup.md) — installation and configuration
- [Skills](docs/skills.md) — task-framework, task-create, log-decision skills
- [Task framework](docs/arch/task_framework.md) — task lifecycle, subtasks, commit flow
