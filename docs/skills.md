# Claude-hooks Skills

Skills live in `skills/<name>/skill.md` and are synced to `~/.claude/skills/<name>/skill.md` after every change. Invoke with `/<name>` in any Claude session.

## Skills index

| Skill | Invoke | Purpose |
|-------|--------|---------|
| `/task-framework` | `/task-framework [description]` | Create + activate a task, explains the full task lifecycle |
| `/task-create` | `/task-create` | Quick reference for `tasks__create` API — types, templates, args |
| `/log-decision` | `/log-decision [text]` | Persist a design decision to the active task's checkpoint |

---

## /task-framework

**When:** Start of any multi-step work. Creates a task, activates it for the session, and defines commit/close order.

**Lifecycle:**
```
tasks__create → tasks__set_active → work → /gc (per subtask) → close task → git push
```

**Key rules:**
- Create + activate before any code change
- One active task per session
- `/gc` per subtask commit — never push while task is open
- Push manually after the parent task closes
- Say `task:<id> done` to auto-close at session end

**Session id:** always from `## Turn state` — there is no MCP tool for this.

**Create signatures:** see `/task-create`

**Closing:**
```python
# Preferred — say in message:
task:<id> done

# Explicit:
mcp__claude-hooks__tasks__finish(task_id="<id>", session_id="<sid>", reason="...")
```

---

## /task-create

**When:** About to call `tasks__create`, or need a reminder of which args to pass.

**Rule:** never pass both `cwd` and `domain` — domain takes precedence. Use `cwd` for dev tasks, `domain` for everything else.

**Signatures:**
```python
# Dev task — cwd auto-detects project name + domain
mcp__claude-hooks__tasks__create(title="...", body="...", cwd="<repo path>")

# Research / non-dev — explicit domain, no cwd
mcp__claude-hooks__tasks__create(title="...", body="...", domain="<domain>")

# Subtask
mcp__claude-hooks__tasks__create(title="...", body="...", cwd="<path>", parent_id="<id>")
```

**Domain values:** `market-intel`, `vault`, `astrology`, `claude-hooks`, `macos`, `global`

**Body format — always start with `Type:`:**

| Type | Required sections |
|------|------------------|
| `feature` | Type, Task, Resolution, Motivation, Files |
| `bug` | Type, Task, Resolution, Cause, Files |
| `research` | Type, Task, Finding, Context, Files |
| `misc` | Type, Task, Resolution, Notes, Files |

The gate in `hooks/dispatcher.py` enforces these sections — missing ones will deny the call with a hint.

---

## /log-decision

**When:** A load-bearing design/architectural choice is made that should survive context compression and future sessions.

**Steps:**
1. Read active task id from `## Active task` — stop if none
2. Compose: **what was chosen and why** (one line, rationale is the key part)
3. Call:
```python
mcp__claude-hooks__tasks__add_decision(
    task_id="<id>",
    decision="<text>",
    session_id="<sid>"
)
```
4. Reply: `Decision logged: "<text>"`

The decision is injected under `## Task decisions` every subsequent turn for that task.

---

## Syncing skills to ~/.claude

After editing any skill file in `skills/`, sync it:
```bash
cp skills/<name>/skill.md ~/.claude/skills/<name>/skill.md
```

The repo is the source of truth — `~/.claude/skills/` is the deployed copy.
