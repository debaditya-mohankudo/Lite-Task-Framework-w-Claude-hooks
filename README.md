# claude-hooks

A persistent memory and task tracking layer for Claude Code — so every session picks up where the last one left off.

## Task framework

Here's the problem: Claude has no memory between sessions. You close the window, come back the next day, and you're starting over — re-explaining what you were building, what you already tried, what the tricky part was. For anything that takes more than one sitting, this gets old fast.

The task framework fixes it. You describe what you want to work on and invoke `/task-framework`. Claude assesses whether it needs to be broken into subtasks, creates the task (or subtasks) using `/task-create`, activates it for the session, and starts tracking — every turn, every tool call, every decision gets logged.

Next session, you just say "continue task:a3f1b2". Claude reads the full history and picks up exactly where you left off — what was done, what was decided, what's still open. No recap, no re-explaining.

Commits get tagged with the task ID too, so the git history ties back to the work log. When something breaks two weeks later, you can trace it back to the exact session where it was introduced.

Here's one from this repo — moving all MCP tools into a self-contained server:

```text
"migrate claude-hooks MCP tools into a standalone server  /task-framework"
  → Claude proposes 3 subtasks: stand up FastMCP server, migrate task tools, cutover config
  → /task-create creates parent task:be7d66a5 + 3 subtasks
  → first subtask activated, tracking begins

...stand up mcp_server.py, wire memory + session tools, commit with /gc...

  → next subtask activated automatically, prior work is in context

...migrate tasks__* tools, add task_edges schema, commit...

  → final subtask: update ~/.claude.json, smoke-test, remove old dispatcher entries

"task:03af8768 done"                      ← parent auto-closes when all subtasks done
```

Three sessions, three subtasks, zero re-explaining. The full development trail — what was done in each session, which files changed, what decisions were made — stays linked end-to-end.

---

## Docs

- [Architecture](docs/ARCHITECTURE.md) — pipeline overview, node graph, data flow
- [Setup](docs/setup.md) — installation and configuration
- [Skills](docs/skills.md) — task-framework, task-create, log-decision skills
- [Task framework](docs/arch/task_framework.md) — task lifecycle, subtasks, commit flow
