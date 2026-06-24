# claude-hooks

## Task Tracking

Use `tasks__*` MCP tools (via `local-mac`) for all multi-step work — instead of TodoWrite. Tasks persist across sessions, surface automatically when referenced, and build a development trail.

- `tasks__create(title, body?)` — start a new task; returns `task:<id>`
- `tasks__create_epic(title, motivation, files?, cwd?, session_id?)` — create an epic without the body-template gauntlet; builds the required body internally
- `tasks__update(id, status?, body?)` — mark `wip` when starting, `done` when finished
- `tasks__list()` — see all open/wip tasks
- `tasks__history(id)` — full turn-by-turn development log for a task

Reference a task as `task:<id>` in any prompt to pin it — it will be injected into context and logged automatically at session end.

Use TodoWrite only for ephemeral within-session tracking (e.g. sub-steps of a single prompt). For anything spanning multiple turns or sessions, use `tasks__*`.

## Running Tests

The hook server runs from the **test worktree** (`~/workspace/claude-hooks-test`, port 8766, `--reload` on). Dev worktree edits never affect the running server.

Run unit tests in dev at any time (fast, no server needed):

```bash
cd ~/workspace/claude-hooks-dev
uv run python -m pytest tests/ -q -m "not integration"
```

To deploy and run the full suite, use `/deploy`.

## Recent Activity / Conversation History

To see "what was I working on?" — recent prompts, MCP tool calls, and activated
tasks as a single chronological timeline — use `hooks__server_memory`:

```text
mcp__claude-hooks__hooks__server_memory(n_events?)
```

The hook server keeps a durable, consolidated context store (SQLite, capped to a
1000-entry rolling window) populated at the HTTP boundary across Claude sessions.
Returns an `events` sequence with timestamps. Useful for cold-start orientation in
a fresh session. Returns `{error}` if the server is down.

## Development Workflow (git worktree)

The hook server runs from `~/workspace/claude-hooks-test` (test branch) with a
**SqliteSaver** checkpoint at `~/.claude/langgraph_checkpoints.db`. State persists to
disk, `--reload` is enabled — file changes to the test worktree are picked up automatically.
It runs on port **8766**.

Develop in the isolated worktree at `~/workspace/claude-hooks-dev` (dev branch):

```bash
# 1. Edit in dev worktree
cd ~/workspace/claude-hooks-dev

# 2. Quick unit tests (no server needed)
uv run python -m pytest tests/ -q -m "not integration"

# 3. Commit
/gc

# 4. Deploy to test + full suite + ship to main
/deploy
```

**Key rules:**

- Edits go in `~/workspace/claude-hooks-dev` (dev branch) — never touch main or test directly
- `/gc` commits target `--repo ~/workspace/claude-hooks-dev`
- Server runs from `claude-hooks-test` — dev edits never disrupt live Claude Code hooks
- main is never touched except by `/deploy`

## Observability

All hook logs write to `claude_hooks.sqlite` in iCloud via `sqlite_log_handler.py`.

**Always use the MCP tool to read logs — never query the DB directly with sqlite3:**

```text
mcp__claude-hooks__hooks__read_logs_sqlite
mcp__local-mac__memory__read_compact
mcp__local-mac__session__list_ids
mcp__local-mac__session__get
```
