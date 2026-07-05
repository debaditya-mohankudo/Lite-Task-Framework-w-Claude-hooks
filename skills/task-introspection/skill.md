---
name: task-introspection
description: Post-task retrospective that improves the engineering system. Review completed work, capture unlogged decisions, identify surprises, evolve memories, concepts, skills, and workflows so future executions become easier. Use when the user says /task-introspection or "retrospect on task:<id>".
user-invocable: true
updated: 2026-07-05
repo: ~/workspace/claude-hooks/skills/task-introspection/skill.md
deployed: ~/.claude/skills/task-introspection/skill.md
---

## Purpose

The purpose of introspection is **not** to remember the past.

The purpose is to make the **next execution better**.

Every completed task generates evidence. Use that evidence to improve:

* memories
* concepts
* skills
* documentation
* tooling
* automation
* engineering workflow

A successful introspection leaves the system slightly more capable than before.

---

## When to invoke

* User runs `/task-introspection` or `/task-introspection task:<id>`
* After `task:<id> done` or `tasks__finish` — can be chained immediately
* User asks: "retrospect", "what did we learn", "encode learnings"

---

## Step 1 — Identify the task

If a task id is supplied, use it. Otherwise use the most recently completed task.

```python
mcp__claude-hooks__tasks__get(id="<task_id>")
mcp__claude-hooks__tasks__history(id="<task_id>")
```

Review: title, original task, resolution, decisions, files changed, turn history, completion summary.

**If the task has no turn history (was never activated),** note that and skip straight to Step 3's decision/surprise questions using only the body diff — there's no turn-by-turn evidence to mine.

---

## Step 2 — Gather context

```python
mcp__claude-hooks__diff_rag__query(query="task:<task_id>", repo=".")
mcp__claude-hooks__memory__search(query="<key concepts from task title/files>")
```

This context is used to understand what changed and what the system now knows — not to pad the report.

---

## Step 3 — Engineering retrospective

Think like a senior engineer. The goal is not to summarize the task — it's to improve the engineering system. Work through each question, answering from task context; don't ask the user unless genuinely unclear.

### 1. Where did uncertainty come from?
Identify what consumed the most engineering effort (architecture, requirements, debugging, testing, deployment, tooling, hidden dependencies). Ask: *could this uncertainty have been removed before implementation?* If yes, recommend how — usually this means "the grooming pass should have caught X."

### 2. What decisions were made but never recorded?
**This is the highest-value part of introspection — never skip it.** Compare the original task, logged `## Task decisions`, turn history, and the actual implementation. For each missing decision:
```python
mcp__claude-hooks__tasks__add_decision(task_id="<id>", decision="<text>", session_id="<sid>")
```
**Never guess the session_id.** Read it from the `## Turn state` system-prompt block when visible. If it isn't visible, use `mcp__claude-hooks__hooks__session_id` rather than inventing one.

### 3. What surprised us?
Identify outcomes neither the task nor related context predicted — unexpected architecture, hidden coupling, framework behavior, API quirks, tool limitations, debugging discoveries. Ask: *should this become durable knowledge?* If yes, continue to Step 4.

### 4. What should exist next time?
Imagine another engineer starting the same task tomorrow — what should already exist? Memory, concept, documentation, task template, automation, MCP tool, workflow, reusable utility. Prefer improving the system over documenting history.

**If the improvement is a new tool or capability**, that's a code task, not something to improvise inline here — see [[skills-use-tools-not-implement-them]]. Recommend it as a follow-up task rather than hand-rolling a one-off script mid-introspection.

### 5. What became unnecessary?
Knowledge should evolve. Identify obsolete memories, stale documentation, outdated workflows, unnecessary process, superseded concepts. Recommend removal or update — **don't apply it unilaterally**: surface the specific item to the user ("memory `<slug>` looks superseded by this task — remove it, or keep for reference?") the same way a duplicate/orphan task gets flagged for confirmation in `/task-grooming`, rather than silently deleting or rewriting. Avoid accumulating stale knowledge, but don't let cleanup skip the user's say either.

---

## Step 4 — Memory evolution

Only encode knowledge likely to help future tasks — workflow discoveries, architectural patterns, debugging techniques, recurring pitfalls, framework behavior. Avoid recording task-specific trivia.

```python
mcp__claude-hooks__memory__add(name="<slug>", type="feedback", domain="<domain>", tags="...", body="...")
```

Link related memories with `[[slug]]` in the body where relevant — cheap now, saves a future search.

---

## Step 5 — Concept store review

Ask: *did this task change how the system should think about this domain?* If yes:

```
Skill(skill="update-concept-store", args="repo=<repo> touched_files=<files> context=<resolution and decisions>")
```

If no concept store exists, silently continue — don't note it as a gap. Only evolve concepts when the task genuinely changes domain understanding, not on every task.

---

## Step 6 — System improvement review

Look beyond the task. Ask: *if this task were repeated tomorrow, what single improvement would save the most effort?* — better skill, automation, MCP tool, prompt, workflow, documentation, memory, concept, or task template.

If a skill appears incomplete or wrong, recommend updating it:
```
Skill gap found: /task-framework step 2 doesn't mention X.
Update it? [yes/no]
```
Do not modify skills automatically unless the user confirms.

---

## Step 7 — Output

```text
## Introspection: task:<id> — <title>

Execution

✓ Smooth
⚠ Minor surprises
✗ Significant deviations

Major Sources of Uncertainty

- ...

New Decisions Captured

- ...

New Knowledge

- ...

Potentially Stale Knowledge

- ...

Recommended System Improvements

- ...

Highest-Leverage Improvement

- <single most valuable improvement>

Overall Assessment

<one concise paragraph>
```

Keep it brief and focused on insights, not chronology. If nothing to encode and everything went smoothly, say so in one line — don't pad. This is a 2-minute activity, not a report.

---

## Rules

- **Never skip decision-logging (Step 3.2).** It's the highest-value part of introspection.
- Don't ask the user to answer questions you can derive from task context.
- **Never guess the session_id.** Read from `## Turn state`, or use `hooks__session_id` if it isn't visible.
- **Never unilaterally delete or rewrite stale memories/skills/concepts.** Flag them and let the user decide — same standard as flagging a duplicate/orphan task in `/task-grooming` rather than closing it unasked.
- **New capabilities are code tasks, not inline scripts.** If Step 3.4 or Step 6 surfaces a missing tool, recommend building it as its own task rather than improvising it during the retrospective.
- Keep the output tight — one line per finding.

---

## Engineering philosophy

Every completed task is evidence. The objective is not to document everything — it's to improve the engineering system.

Ask yourself:

* What slowed us down?
* What surprised us?
* What assumptions proved wrong?
* What knowledge became durable?
* What knowledge became obsolete?
* What should change before another engineer performs this task?

Prefer improving the system over recording history. A successful introspection should make the next execution faster, more predictable, and less dependent on individual experience.
