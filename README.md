# claude-hooks

A persistent memory and task tracking layer for Claude Code — so every session picks up where the last one left off.

## Task framework

Here's the problem: Claude has no memory between sessions. You close the window, come back the next day, and you're starting over — re-explaining what you were building, what you already tried, what the tricky part was. For anything that takes more than one sitting, this gets old fast.

The task framework fixes it. You start a task with `/task-framework`, and Claude creates a persistent record: every turn, every tool call, every decision. Next session, you just say "continue task:a3f1b2" and Claude reads the history — no recap needed, straight back to work.

Commits get tagged with the task ID too, so the git history ties back to the work log. When something breaks two weeks later, you can trace it.

```text
"add rate limiting to the API  /task-framework"   ← starts tracking
...work, commit with /gc, close the window...
"continue task:a3f1b2"                            ← picks up exactly where you left off
...finish the work...
"task:a3f1b2 done"                                ← closes cleanly
```

---

## Docs

- [Architecture](docs/ARCHITECTURE.md) — pipeline overview, node graph, data flow
- [Setup](docs/setup.md) — installation and configuration
- [Skills](docs/skills.md) — task-framework, task-create, log-decision skills
- [Task framework](docs/arch/task_framework.md) — task lifecycle, subtasks, commit flow
