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

If the task touched files in the claude-hooks repo, check whether the changes are reflected in the concept store:

```python
import json
from pathlib import Path
concepts = json.loads(Path("/Users/debaditya/workspace/claude-hooks-dev/concept_store/concepts.json").read_text())
# match against files from task body Files: section or commits
hits = [c for c in concepts.values() if c["module"] in touched_files]
```

For each matched concept, ask: does the task's resolution change any invariant, contract, or description for that module?

- **No change** → note "concepts still valid", skip
- **Mismatch found** → update the concept in place:

```python
import json
from pathlib import Path

store_path = Path("/Users/debaditya/workspace/claude-hooks-dev/concept_store/concepts.json")
concepts = json.loads(store_path.read_text())

# Update only the fields that changed — leave others intact
concepts["<concept-name>"]["invariants"] = ["updated invariant 1", ...]
concepts["<concept-name>"]["contracts"] = ["updated contract 1", ...]
concepts["<concept-name>"]["description"] = "updated description"
concepts["<concept-name>"]["confidence"] = 0.97

store_path.write_text(json.dumps(concepts, indent=2))
print("Updated concept: <concept-name>")
```

Run this via the Write tool (write to a temp script) then Bash — avoid inline heredocs that may trigger gates.

After updating, commit the changed `concepts.json` to dev:

```bash
git -C ~/workspace/claude-hooks-dev add concept_store/concepts.json
# then /gc with task:<id>
```

Skip silently if `concepts.json` doesn't exist or no files match.

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
