---
name: log-decision
description: Log an explicit design decision for the active task. Persists it to task_events and appends to mid_task_decisions in the checkpoint so it is injected every subsequent turn. Use when the user says "log this decision", "remember this choice", or invokes /log-decision.
user-invocable: true
---

# /log-decision

Log a load-bearing design decision for the active task so it persists across all remaining turns and future sessions.

## When to invoke

- User says "log this decision", "remember this choice", "note this"
- User explicitly invokes `/log-decision`
- A significant architectural or design choice was just made that affects future work

## Steps

### 1. Get active task and session

Read from `## Active task` and `## Turn state` in the system prompt. If no active task, tell the user and stop.

### 2. Compose the decision text

If the user provided text after `/log-decision`, use it verbatim.
Otherwise, summarise the decision in one line: **what was chosen and why** (rationale is the most important part).

Good: "Chose opaque tokens over JWT — avoids key rotation complexity on short-lived sessions"
Bad: "Using opaque tokens"

### 3. Call tasks__add_decision

```python
mcp__local-mac__tasks__add_decision(
    task_id="<active_task_id>",
    decision="<one-line decision with rationale>",
    session_id="<session_id>"
)
```

### 4. Confirm

Reply: `Decision logged: "<decision text>"`

The decision will appear under `## Task decisions` in every subsequent turn's system prompt for this task.
