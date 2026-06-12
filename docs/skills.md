# Claude-hooks Skills

Skills live in `skills/<name>` and are synced to `~/.claude/skills/<name>` after every change. Invoke with `/<name>` in any Claude session.

## Skills index

| Skill | Invoke | Purpose |
| --- | --- | --- |
| `/task-framework` | `/task-framework [description]` | Create + activate a task, explains the full task lifecycle |
| `/jira-task-create` | `/jira-task-create` | Jira-style issue creation — epic/story/task/bug/subtask hierarchy, templates, args |
| `/log-decision` | `/log-decision [text]` | Persist a design decision to the active task's checkpoint |
| `/gc` | `/gc` | Commit without pushing; appends `task:<id>` to commit body while a task is active |
| `/pause` | `/pause` | Finish current action, save pending intent to task body, wait for user input |
| `/switch-project` | `/switch-project [domain]` | Override session domain; prompts with list if no argument given |

---

## /task-framework

**When:** Start of any multi-step work. Creates a task, activates it for the session, and defines commit/close order.

**Lifecycle:**

```text
tasks__create → tasks__set_active → [pre-impl review] → work → /gc (per subtask) → close task → git push
```

**Pre-implementation review (grooming):** after all subtasks are created, activate each one and evaluate the plan against injected context (related tasks, code chunks, memories). Update bodies with gaps found, resolve design decisions, reset status to open. Skip only for single-task work.

**Key rules:**

- Create + activate before any code change
- One active task per session
- `/gc` per subtask commit — never push while task is open
- Push manually after the parent task closes
- Say `task:<id> done` to auto-close at session end

**Session id:** always from `## Turn state` — there is no MCP tool for this.

**Create signatures:** see `/jira-task-create`

**Closing:**

```python
# Preferred — say in message:
task:<id> done

# Explicit:
mcp__claude-hooks__tasks__finish(task_id="<id>", session_id="<sid>", reason="...")
```

---

## /jira-task-create

**When:** About to call `tasks__create`, or need a reminder of which args to pass.

**Rule:** never pass both `cwd` and `domain` — domain takes precedence. Use `cwd` for dev tasks, `domain` for everything else.

**Jira hierarchy:** `epic → story / task / bug → subtask`

**Signatures:**

```python
# Epic — top-level initiative, no parent
mcp__claude-hooks__tasks__create(title="...", body="...", cwd="<repo path>", issue_type="epic")

# Story / task / bug — child of an epic
mcp__claude-hooks__tasks__create(title="...", body="...", cwd="<path>", parent_id="<epic_id>", issue_type="story")

# Subtask — must have a parent
mcp__claude-hooks__tasks__create(title="...", body="...", cwd="<path>", parent_id="<id>", issue_type="subtask")

# Research / non-dev — explicit domain, no cwd
mcp__claude-hooks__tasks__create(title="...", body="...", domain="<domain>")
```

**Domain values:** `market-intel`, `vault`, `astrology`, `claude-hooks`, `macos`, `global`, `misc`

**`issue_type` param** (Jira terminology, separate from body): `epic` | `story` | `task` | `bug` | `subtask` — default `task`.

**Body format — always start with `Type:` (workflow kind, not issue_type):**

| Type | Required sections |
| --- | --- |
| `feature` | Task, Resolution, Motivation, Files |
| `bug` | Task, Resolution, Cause, Files |
| `research` | Task, Finding, Context, Files |
| `misc` | Task, Resolution, Notes, Files |

The gate in `hooks/dispatcher.py` enforces these sections — missing ones will deny the call with a hint.

---

## /log-decision

**When:** A load-bearing design/architectural choice is made that should survive context compression and future sessions.

**Steps:**

**1.** Read active task id from `## Active task` — stop if none

**2.** Compose: **what was chosen and why** (one line, rationale is the key part)

**3.** Call:

```python
mcp__claude-hooks__tasks__add_decision(
    task_id="<id>",
    decision="<text>",
    session_id="<sid>"
)
```

**4.** Reply: `Decision logged: "<text>"`

The decision is injected under `## Task decisions` every subsequent turn for that task.

---

## /gc

**When:** Committing work mid-task. Never pushes — push is a deliberate end-of-task action.

**What it does:**

- Derives the commit message from session context (no prompt needed)
- Appends `task:<id>` to the commit body if a task is active
- Runs tests before committing if a `tests/` directory exists
- Refreshes the code graph and embeddings after a successful commit

**Commit order:**

```text
implement → /gc (per subtask) → close task → git push
```

**With explicit task id** (overrides active task):

```text
/gc task:abc123
```

---

## /pause

**When:** User wants to redirect mid-session without losing in-flight context.

**Steps:**

**1.** Finish whatever tool call is in flight — never abort mid-action

**2.** If an active task exists, save pending items via the dedicated tool:

```python
mcp__claude-hooks__tasks__pause(
    task_id="<id>",
    pending=["<item 1>", "<item 2>"],
    session_id="<sid>"
)
```

**3.** Output pause signal:

```text
Paused. [What was just completed.]

Pending (saved to task:<id>):
- <item 1>
- <item 2>

Waiting for your input.
```

**4.** Stop — no further reasoning or proposals.

The `## Pending before paused` section is overwritten on each invoke (most-recent state only). Task stays active; history continues when user resumes.

---

## /switch-project

**When:** CWD doesn't map to the right domain, or working across repos in one session.

**If no argument given** — show numbered list and wait:

```text
Available domains:
1. claude-hooks  2. vault  3. market-intel
4. astrology     5. macos  6. global  7. misc

Which domain? (or "clear" to revert to CWD detection)
```

**Steps:**

**1.** Read `session_id` from `## Turn state`

**2.** Validate domain against `VALID_DOMAINS` in `src/config.py`

**3.** Run:

```bash
cd ~/workspace/claude-hooks && uv run python scripts/task_activate.py switch_project <domain> <session_id>
```

**4.** Confirm: `Switched to project domain: <domain>`

Pass `""` (or say `clear`) to revert to CWD-based detection. The override persists in the LangGraph checkpoint for the session — resets when a new session starts.

---

## Syncing skills to ~/.claude

After editing any skill file in `skills/`, sync it:

```bash
cp skills/<name> ~/.claude/skills/<name>
```

The repo is the source of truth — `~/.claude/skills/` is the deployed copy.

---

← [Architecture](ARCHITECTURE.md) · [Task Framework](arch/task_framework.md) · [New Repo Onboarding](new_repo_onboarding.md)
