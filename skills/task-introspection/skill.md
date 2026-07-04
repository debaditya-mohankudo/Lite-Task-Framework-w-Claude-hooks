---
name: task-introspection
description: Post-task retrospective — runs after a task closes to surface unlogged decisions, stale memories, skill gaps, and encode learnings back into the system. Use when the user says /task-introspection or "retrospect on task:<id>".
user-invocable: true
updated: 2026-06-24
repo: ~/workspace/claude-hooks/skills/task-introspection/skill.md
deployed: ~/.claude/skills/task-introspection/skill.md
---

Run a lightweight retrospective on a recently closed task. Feeds learnings back into memories, skills, and docs before moving on.

## When to invoke

- User says `/task-introspection` or `/task-introspection task:<id>`
- After `task:<id> done` or `tasks__finish` — can be chained immediately
- Any time the user says "retrospect", "what did we learn", "encode learnings"

## Steps

### 1. Identify the task

If a task id was passed as argument, use it. Otherwise use the most recently closed task:

```python
mcp__claude-hooks__tasks__get(id="<task_id>")
mcp__claude-hooks__tasks__history(id="<task_id>")
```

Read: title, body (Resolution section), decisions logged, turn count, files touched.

### 2. Pull related context

```python
# Commits that reference this task
mcp__claude-hooks__diff_rag__query(query="task:<task_id>", repo=".")

# Memories that may be stale given what changed
mcp__claude-hooks__memory__search(query="<key concept from task title/files>")
```

### 3. Ask four questions

Work through these — answer each from the task context, don't ask the user unless genuinely unclear:

**Q1 — Did it go as planned?**
Compare the original `Task:` section against the `Resolution:` section. Note any scope creep, pivots, or steps that were harder than expected.

**Q2 — What decisions were made but not logged?**
Scan turn history for choices that aren't in `## Task decisions`. If any — log them now:
```python
mcp__claude-hooks__tasks__add_decision(task_id="<id>", decision="<text>", session_id="<sid>")
```

**Q3 — Are any memories or docs now stale?**
From the files changed and commit message, identify concepts that have memories. Check each:
```python
mcp__claude-hooks__memory__search(query="<concept>")
```
Flag stale ones to the user: `"Memory <slug> may be stale — still accurate?"`

**Q4 — What should be encoded for next time?**
Identify non-obvious learnings — workflow gotchas, process gaps, tool behaviours discovered. For each worth keeping:
```python
mcp__claude-hooks__memory__add(name="<slug>", type="feedback", domain="<domain>", tags="...", body="...")
```

### 4. Concept store audit

Delegate to `/update-concept-store` — it detects whether the task's repo uses the JSON format (claude-hooks-dev) or the SQLite format (SeniorDevAgent) and applies the right update semantics for each, rather than this skill assuming one format:

```
Skill(skill="update-concept-store", args="repo=<repo path from task context> touched_files=<from task body Files: section or commits> context=<task Resolution section / what changed and why>")
```

Relay its summary output as-is into this skill's own output (see step 6). If it reports "no concept store found," treat that the same as the old "skip silently" behavior — don't note it as a gap.

### 5. Check skill/doc gaps

If the task revealed a missing or wrong step in any skill (`/task-framework`, `/task-create`, `/gc`, `/deploy`), note it:

```
Skill gap found: /task-framework step 2 doesn't mention X.
Update it? [yes/no]
```

Only update if the user confirms.

### 6. Output summary

```
## Introspection: task:<id> — <title>

**Went as planned:** yes / mostly / no — <one line on what differed>

**Decisions logged:** <N> already logged, <M> added now
  - "<decision text>"

**Stale memories:** <slug> — <why stale>

**Learnings encoded:** <N>
  - <slug>: <one-line summary>

**Skill gaps:** none / <skill> — <what's missing>
```

If nothing to encode and everything went smoothly, say so in one line — don't pad.

## Rules

- Never skip Q2 (unlogged decisions) — this is the highest-value step.
- Don't ask the user to answer questions you can derive from task context.
- Keep the output tight — one line per finding. This is a 2-minute activity, not a report.
- If the task has no turn history (was never activated), note that and skip Q1/Q2.
