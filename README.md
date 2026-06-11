# claude-hooks

A persistent memory and task tracking layer for Claude Code — so every session picks up where the last one left off.

## Task framework

Every Claude session starts cold. Without task tracking, you re-explain what you were doing, what you tried, and what's left — every time. For work that spans days, that overhead compounds into real friction.

The task framework eliminates that. Claude tracks what happens each turn, across sessions. When you come back, it already knows where you left off.

**End-to-end flow:**

```text
Day 1 — starting fresh

  You:   "add rate limiting to the API  /task-framework"
  Claude: creates the task, activates it, starts tracking

  ...write code, hit a snag, make decisions...

  You:   "/gc"
  Claude: commits with task:<id> in the commit body

  ...session ends...


Day 2 — picking up where you left off

  You:   "continue task:a3f1b2"
  Claude: already knows — what was built, what was decided, what's left
          no re-explaining needed

  ...finish the work...

  You:   "task:a3f1b2 done"
  Claude: closes the task, clears the session context
```

Every commit gets a task reference. Every decision is in the history. The development trail is coherent end-to-end.

---

## Docs

- [Architecture](docs/ARCHITECTURE.md) — pipeline overview, node graph, data flow
- [Setup](docs/setup.md) — installation and configuration
- [Skills](docs/skills.md) — task-framework, task-create, log-decision skills
- [Task framework](docs/arch/task_framework.md) — task lifecycle, subtasks, commit flow
