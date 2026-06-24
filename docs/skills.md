---
tags: skills, /gc, /task-framework, /jira-task-create, /log-decision, /pause, /onboarding, /what-am-i-working-on, skill index, slash commands, git commit skill, task creation skill, decision logging, workflow skills
---
# Claude-hooks Skills

Skills live in `skills/<name>` and are synced to `~/.claude/skills/<name>` after every change. Invoke with `/<name>` in any Claude session.

## Skills index

| Skill | Invoke | Purpose |
| --- | --- | --- |
| `/gc` | `/gc [task:<id>]` | Git commit with automatic task tagging, test run, and code graph refresh |
| `/task-framework` | `/task-framework [description]` | Create + activate a task, explains the full task lifecycle |
| `/jira-task-create` | `/jira-task-create` | Jira-style issue creation — epic/story/task/bug/subtask hierarchy, templates, args |
| `/log-decision` | `/log-decision [text]` | Persist a design decision to the active task's checkpoint |
| `/pause` | `/pause` | Finish current action, save pending intent to task body, wait for user input |
| `/onboarding` | `/onboarding` | Interactive setup guide — walks a new teammate through full claude-hooks setup step by step |
| `/what-am-i-working-on` | `/what-am-i-working-on` | Cold-start orientation — recent prompts, tool calls, and task activations across sessions |
| `/deploy` | `/deploy` | Deploy claude-hooks dev→test→main — unit gate, full suite, then ship to main |

---

## /deploy

**When:** Ready to ship a completed feature from dev to production (main).

**Steps:**

**1.** Deploy dev → test and run full suite:

```bash
~/workspace/claude-hooks/scripts/deploy.sh
```

This script:

- Runs unit tests in dev (`-m "not integration"`) as a quick gate
- Merges dev → test (server auto-reloads via `--reload`)
- Waits for health check at `http://127.0.0.1:8766/health`
- Runs the full test suite (unit + integration) from the test worktree

Stop and report any failure — do not proceed to step 2.

**2.** Ship test → main:

```bash
~/workspace/claude-hooks/scripts/deploy.sh --ship
```

Merges test → main. No tests run here — they already passed in step 1.

**3.** Report:

```text
✓ Deployed to main.
  Unit gate:   passed (dev)
  Full suite:  passed (test)
  main is now at: <git log --oneline -1>
```

**Rules:**

- Never skip the unit gate or full suite
- If the health check fails after merge, stop — the server didn't reload cleanly
- If integration tests fail, stop and report which tests failed; do not ship to main
- Only applies to the `claude-hooks` project (worktrees at `~/workspace/claude-hooks-dev`, `~/workspace/claude-hooks-test`, `~/workspace/claude-hooks`)

---

## /gc

**When:** After completing a logical unit of work — typically one subtask. Never pushes; push is a deliberate end-of-task action.

**Gate:** `GitCommitGate` in `hooks/gates.py` blocks any `git commit` or `git_local.sh` call that lacks a `task:<id>` in the commit message body. `/gc` satisfies this automatically.

**Steps:**

**1.** Determine task id — from argument `/gc task:abc123`, or from `## Active task` in system prompt

**2.** Run tests if `tests/` exists:

```bash
uv run python -m pytest tests/ -q
```

Stop and report failures — do not commit on a red test run.

**3.** Commit via `git_local.sh`:

```bash
~/workspace/claude_for_mac_local/tools/git_local.sh -y [--repo <path>] "feat(area): description

task:<id>"
```

**4.** Refresh code graph + embeddings for changed files (incremental):

```python
mcp__claude-hooks__code_rag__index_files(files=["<changed>.py", ...])
```

**5.** Confirm: `✓ Committed: "feat(area): description"`

**Grouping:** when a session touched multiple distinct tasks, propose one commit per task before committing — get confirmation, then commit each group with `git add <files> && git commit`.

**Target repo:** determined from context — vault edits go to `--repo ~/workspace/claude_documents`, current project changes omit `--repo`.

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

**Checklist format in Resolution:** For removal, refactor, or any task with 3+ discrete file/step targets, write `Resolution:` as a markdown checklist rather than prose. Tick items with `- [x]` via `tasks__update(body=...)` as each step completes — makes the task body a live progress tracker.

```text
Resolution:
- [ ] src/tools/tasks.py — remove review entries
- [ ] hooks/gates.py — remove review gate
- [ ] delete load_active_review.py
```

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

## /what-am-i-working-on

**When:** Start of a fresh session — need quick orientation on what was being worked on.

**Steps:**

**1.** Call:

```python
mcp__claude-hooks__hooks__server_memory(n_events=50)
```

**2.** Present the returned markdown table directly — no transformation needed.

**3.** If the tool returns `{error: ...}`, report the server is unreachable and suggest:

```bash
launchctl list | grep claude-hooks
```

---

## /onboarding

**When:** A new teammate is setting up claude-hooks for the first time.

**Steps:** OS detection → prerequisites → clone/deps → iCloud databases → hooks registration → MCP server → smoke test → done.

Goes one step at a time, waiting for confirmation before proceeding. Fills in real paths (username, repo dir) automatically.

**Reference:** `docs/setup.md` · `docs/new_repo_onboarding.md`

---

## Syncing skills to ~/.claude

After editing any skill file in `skills/`, sync it:

```bash
cp skills/<name> ~/.claude/skills/<name>
```

The repo is the source of truth — `~/.claude/skills/` is the deployed copy.

---

← [Architecture](ARCHITECTURE.md) · [Task Framework](arch/task_framework.md) · [New Repo Onboarding](new_repo_onboarding.md)
