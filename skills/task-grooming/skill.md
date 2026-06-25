---
name: task-grooming
description: Pre-work grooming pass on one or more tasks. Activates each task to pull related context, audits the body for gaps, updates with findings, and reports readiness. Use before starting a task or sprint. Invoke with /task-grooming, /task-grooming task:<id>, or /task-grooming epic:<id>.
user-invocable: true
updated: 2026-06-25
repo: ~/workspace/claude-hooks/skills/task-grooming/skill.md
deployed: ~/.claude/skills/task-grooming/skill.md
---

Pre-implementation grooming pass. Activates each task to pull related-task context, diff RAG, and scored memories — then audits the body against that context and updates gaps before work begins.

## When to invoke

- User says `/task-grooming` or `/task-grooming task:<id>` or `/task-grooming epic:<id>`
- Before activating a task for real work ("let's groom this first")
- After creating a set of subtasks before starting the first one

## Input resolution

| Invocation | What to groom |
|---|---|
| `/task-grooming` | List open/blocked tasks, ask user which to groom |
| `/task-grooming task:<id>` | Single task |
| `/task-grooming epic:<id>` | All open/blocked children of that epic |
| `/task-grooming task:<id1> task:<id2>` | Explicit list |

## Steps

### 1. Resolve the task list

```python
# Single task
mcp__claude-hooks__tasks__get(id="<id>")

# Children of an epic
mcp__claude-hooks__tasks__list()  # filter by parent_id == epic_id
```

If no argument given, call `tasks__list()` and ask which tasks to groom.

### 2. For each task — activate and read context

```python
mcp__claude-hooks__tasks__set_active(task_id="<id>", session_id="<session_id from Turn state>")
```

Wait for the PostToolUse bridge to write the checkpoint (activation is synchronous). The next turn will have injected:

- `## Active task` — body, decisions
- `## Related tasks` — top-3 semantically similar past tasks
- `## Related commits` — top-3 diff hunks
- `## Task RAG` — top-3 code modules

Read all four sections. These are your grooming inputs — don't skip them.

### 3. Audit the body against injected context

Run all six checks. Flag any that fail:

| Check | Pass condition | Flag |
|---|---|---|
| **Resolution format** | `Resolution:` section exists and is a checklist (`- [ ]`) | "prose — convert to checklist" |
| **File paths named** | Each checklist item names a file or module | "file paths missing" |
| **Dependencies stated** | If task needs another task first, it's noted | "dependency on X not stated" |
| **Related task conflicts** | No related task contradicts the plan | "conflicts with task:<id> — <what>" |
| **Prior art reused** | Related tasks surface relevant patterns already in code | "note prior art from task:<id>" |
| **Design decisions deferred** | No "TBD" where a concrete decision is needed to start | "decision needed: <what>" |

### 4. Update the body with gaps found

For each flag, append a note to the task body:

```python
mcp__claude-hooks__tasks__update(
    id="<task_id>",
    body="<existing body>\n\n## Grooming notes (2026-MM-DD)\n- <flag 1>\n- <flag 2>"
)
```

If no flags — no update needed, note "ready as-is".

### 5. Reset status to open

After grooming, if the task drifted to `active` during activation, reset it:

```python
mcp__claude-hooks__tasks__update(id="<task_id>", status="open")
```

The task is not being worked on yet — grooming is pre-work review, not execution.

### 6. Output summary

One line per task:

```
✓ task:abc — ready  (title)
⚠ task:def — 2 gaps: file paths missing, decision needed: storage format  (title)
⚠ task:ghi — conflicts with task:xyz re: same function  (title)
```

Then a single line: `N tasks groomed — M ready, K need updates.`

If gaps were found, suggest: "Fix the flagged items and re-run `/task-grooming` before activating."

## Rules

- **Activation is mandatory.** Related-task and diff-RAG context is only injected when a task is active. Grooming without activating is reading the body in isolation — useless.
- **Reset to open after grooming.** A groomed task is not a started task. Activation during grooming is a tool, not a status change.
- **Don't rewrite the body — append.** Preserve the original task intent; add grooming notes as a dated section at the bottom.
- **Never guess the session_id.** Always read from `## Turn state`.
- **One task at a time.** Activate, audit, update, reset — then move to the next. Don't batch activations.
