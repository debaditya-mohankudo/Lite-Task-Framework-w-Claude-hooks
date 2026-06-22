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

```bash
uv run python -m pytest tests/ -v
uv run python -m pytest tests/test_session_tools.py -v   # session tools only
```

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

The production hook server runs from `~/workspace/claude-hooks` (main branch) with a
**SqliteSaver** checkpoint at `~/.claude/langgraph_checkpoints.db`. State persists to
disk, so `--reload` is safe and enabled — file changes are picked up automatically.

Develop in the isolated worktree at `.claude/dev/` inside this repo (dev branch):

```bash
# All development happens here — server is unaffected
cd ~/workspace/claude-hooks/.claude/dev

# Edit, test, iterate
uv run python -m pytest tests/ -q

# When ready to ship → merge dev→main + restart server in one step
~/workspace/claude-hooks/scripts/deploy.sh
```

**Key rules:**
- Edits go in `.claude/dev/` (dev branch), not the repo root (main)
- `/gc` commits target `--repo ~/workspace/claude-hooks-dev`
- `deploy.sh` runs tests, merges dev→main, restarts launchd server, verifies `/health`
- The server auto-reloads on file changes (`--reload` is on); `deploy.sh` is for merging and a clean restart

## Observability

All hook logs write to `claude_hooks.sqlite` in iCloud via `sqlite_log_handler.py`.

**Always use the MCP tool to read logs — never query the DB directly with sqlite3:**

```text
mcp__claude-hooks__hooks__read_logs_sqlite
mcp__local-mac__memory__read_compact
mcp__local-mac__session__list_ids
mcp__local-mac__session__get
```
