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

### 3. Concept store lookup (claude-hooks repo only)

If the task is in the claude-hooks project (cwd or domain tag), read the concept store:

```python
import json
from pathlib import Path
concepts = json.loads(Path("/Users/debaditya/workspace/claude-hooks-dev/concept_store/concepts.json").read_text())
```

**Prefer the `Concepts:` section in the task body** — if it lists concept slugs, look those up directly. Fall back to matching `Files:` section against `concept["module"]` if `Concepts:` is absent.

Surface matched concepts in the audit:

- **Invariant conflict**: does the task plan violate any stored invariant for that module?
- **Contract break**: does the plan change what the module promises callers (contracts)?
- **New concept**: does the task introduce behavior not captured in any concept for this module?

Add a `## Concept context` block to the grooming notes for any file with stored concepts:

```
## Concept context
- hooks/gates.py: gates-prereq-chain-enforcement
  invariants: ["Gates fail open on DB errors", "External gates never override internal ones"]
  → check: does this task's change respect these invariants?
```

Skip silently if `concepts.json` does not exist (store not yet seeded).

### 4. Audit the body against injected context

Run all six checks. Flag any that fail:

| Check | Pass condition | Flag |
|---|---|---|
| **Resolution format** | `Resolution:` section exists and is a checklist (`- [ ]`) | "prose — convert to checklist" |
| **File paths named** | Each checklist item names a file or module | "file paths missing" |
| **Dependencies stated** | If task needs another task first, it's noted | "dependency on X not stated" |
| **Related task conflicts** | No related task contradicts the plan | "conflicts with task:<id> — <what>" |
| **Prior art reused** | Related tasks surface relevant patterns already in code | "note prior art from task:<id>" |
| **Design decisions deferred** | No "TBD" where a concrete decision is needed to start | "decision needed: <what>" |
| **Concept invariant respected** | Task plan does not violate stored invariants for touched files | "invariant risk: <module> — <invariant>" |

### 5. Update the body with gaps found

For each flag (including concept invariant risks), append a note to the task body:

```python
mcp__claude-hooks__tasks__update(
    id="<task_id>",
    body="<existing body>\n\n## Grooming notes (2026-MM-DD)\n- <flag 1>\n- <flag 2>\n\n## Concept context\n- <module>: <concept-name>\n  invariants: [...]\n  → <observation>"
)
```

If no flags and no relevant concepts — no update needed, note "ready as-is".

### 6. Reset status to open

After grooming, if the task drifted to `active` during activation, reset it:

```python
mcp__claude-hooks__tasks__update(id="<task_id>", status="open")
```

The task is not being worked on yet — grooming is pre-work review, not execution.

### 7. Output summary

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
