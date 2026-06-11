# claude-hooks

A persistent memory and task tracking layer for Claude Code — so every session picks up where the last one left off.

## Task framework

Here's the problem: Claude has no memory between sessions. You close the window, come back the next day, and you're starting over — re-explaining what you were building, what you already tried, what the tricky part was. For anything that takes more than one sitting, this gets old fast.

The task framework fixes it. You describe what you want to work on and invoke `/task-framework`. Claude assesses whether it needs to be broken into subtasks, creates the task (or subtasks) using `/task-create`, activates it for the session, and starts tracking — every turn, every tool call, every decision gets logged.

Next session, you just say "continue task:a3f1b2". Claude reads the full history and picks up exactly where you left off — what was done, what was decided, what's still open. No recap, no re-explaining.

Commits get tagged with the task ID too, so the git history ties back to the work log. When something breaks two weeks later, you can trace it back to the exact session where it was introduced.

```text
"add rate limiting to the API  /task-framework"
  → Claude assesses scope, proposes subtasks if needed
  → /task-create creates the task with context and goals
  → task activated, tracking begins

...work across files, hit decisions, commit with /gc...

"continue task:a3f1b2"                    ← next day, new session
  → Claude reads turn history, resumes without re-explaining

"task:a3f1b2 done"                        ← closes cleanly, clears context
```

---

## Docs

- [Architecture](docs/ARCHITECTURE.md) — pipeline overview, node graph, data flow
- [Setup](docs/setup.md) — installation and configuration
- [Skills](docs/skills.md) — task-framework, task-create, log-decision skills
- [Task framework](docs/arch/task_framework.md) — task lifecycle, subtasks, commit flow
