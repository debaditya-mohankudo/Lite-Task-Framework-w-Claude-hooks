# claude-hooks

A personal layer that runs alongside Claude Code to give it persistent memory, multi-session task continuity, and guardrails against irreversible mistakes.

---

## The perspective

Jira was the right idea. But it was always human-operated and AI-opaque. Now agents can close that gap natively.

Jira was the original development graph. It worked for teams. Epics, tasks, subtasks — every piece of work traced to a reason.

But Jira content is human-readable, not agent-readable. It breaks the moment an AI tries to operate on it without the right tooling around it.

Jira does expose MCP tools now. The graph can technically be agent-operated. But it's heavyweight — the infrastructure, the licensing, the setup — built for organisations, not for a solo developer running an AI-assisted workflow.

The real unlock is agents that create the full epic graph from a requirement, evaluate each item, and execute — with task:<id> in every commit, tying every change to a coherent piece of work. Nothing unattributed. Nothing untraceable.

That's not a workaround for bad process. It's the natural evolution of what Jira was always trying to do — lightweight, native, and built around how AI-assisted development actually works.
I've been building exactly this - a lightweight agent-native development grapah for solo developer. 
👉 github.com/debaditya-mohankudo/claude-hooks

---


## What it looks like in practice

Start a task:

```
migrate the auth module to use the new token schema  /task-framework
```

Claude proposes subtasks, creates them, activates the first one, and starts tracking. You work, commit with `/gc`, and the task ID attaches to each commit automatically.

Next session:

```
continue task:be7d66a5
```

Claude reads the full turn history — what was built, what was decided, what's still open — and picks up without any re-explaining.

Three sessions, three subtasks, zero recap.

---

## Skills

| Skill | What it does |
|-------|--------------|
| `/task-framework` | Start a tracked task — creates subtasks, activates the first, begins logging |
| `/jira-task-create` | Create Jira-style issues — epic / story / task / bug / subtask with hierarchy rules |
| `/gc` | Commit with automatic task tagging and pre-commit test run |
| `/log-decision` | Persist a key design decision to the active task so it survives future sessions |
| `/pause` | Finish the current action, save pending intent to the active task, and wait for user input |

---

## Docs

- [Architecture](docs/ARCHITECTURE.md) — how the pipeline is structured and why
- [Setup](docs/setup.md) — installation and configuration
- [Skills](docs/skills.md) — full skill reference
