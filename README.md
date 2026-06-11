# claude-hooks

A personal layer that runs alongside Claude Code to give it persistent memory, multi-session task continuity, and guardrails against irreversible mistakes.

---

## The problem

Claude Code is powerful but stateless. Every session starts from zero — you re-explain the codebase, re-describe what you were building, re-establish what you already tried. For anything that spans more than one sitting, this friction compounds fast.

Beyond memory loss, there are two other friction points that surface in practice:

**You repeat yourself.** Claude has no record of past decisions, so it rediscovers the same answers, sometimes to different conclusions.

**Irreversible actions happen silently.** Without awareness of what has (or hasn't) already been checked, Claude can call tools that write, delete, or send — with no guardrail.

---

## What this does

**Remembers what matters across sessions.**
Before every prompt, relevant facts from past sessions are automatically surfaced — project context, design decisions, prior approaches, known pitfalls. You stop explaining things twice.

**Tracks work across sessions.**
When you start a multi-session task, Claude logs every turn, decision, and outcome. Next session, pick up with `task:<id>` and Claude reads the full history — what was done, what was decided, what's still open. No recap required.

**Links work to your git history.**
When you commit during an active task, the task ID is appended to the commit body automatically. The git log stays connected to the work log.

**Guards against irreversible tool calls.**
Certain tool calls — ones that write, delete, or send — are blocked unless a prerequisite check has already run in the same session. The gate prevents Claude from acting on stale or unverified state.

**Surfaces the right context without prompting.**
Based on what you're working on, relevant tools and domain knowledge are surfaced at the start of each turn. Less hunting, more doing.

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

---

## Docs

- [Architecture](docs/ARCHITECTURE.md) — how the pipeline is structured and why
- [Setup](docs/setup.md) — installation and configuration
- [Skills](docs/skills.md) — full skill reference
