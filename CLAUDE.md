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

The hook server runs from the **dev worktree** (port 8766, `--reload` on). Run all tests here — unit, integration, and UI — before deploying:

```bash
cd ~/workspace/claude-hooks-dev
uv run python -m pytest tests/ -q                          # full suite
uv run python -m pytest tests/test_session_tools.py -v    # specific file
```

Deploy to main only when tests pass:

```bash
/gc
~/workspace/claude-hooks/scripts/deploy.sh   # merges dev→main; server stays on dev worktree
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

The hook server runs from `~/workspace/claude-hooks-dev` (dev branch) with a
**SqliteSaver** checkpoint at `~/.claude/langgraph_checkpoints.db`. State persists to
disk, `--reload` is enabled — file changes are picked up automatically.
It runs on port **8766**.

Develop in the isolated worktree at `~/workspace/claude-hooks-dev` (dev branch):

```bash
# 1. Edit in dev worktree
cd ~/workspace/claude-hooks-dev

# 2. Run full test suite (server is already running from dev worktree)
uv run python -m pytest tests/ -q

# 3. Commit
/gc

# 4. Deploy → merges dev→main (server stays on dev, no restart needed)
~/workspace/claude-hooks/scripts/deploy.sh
```

**Key rules:**

- Edits go in `~/workspace/claude-hooks-dev` (dev branch), not `~/workspace/claude-hooks` (main)
- `/gc` commits target `--repo ~/workspace/claude-hooks-dev`
- All tests (unit, integration, UI) run in dev worktree — server runs there
- `deploy.sh` merges dev→main; no server restart needed since server runs from dev

## Observability

All hook logs write to `claude_hooks.sqlite` in iCloud via `sqlite_log_handler.py`.

**Always use the MCP tool to read logs — never query the DB directly with sqlite3:**

```text
mcp__claude-hooks__hooks__read_logs_sqlite
mcp__local-mac__memory__read_compact
mcp__local-mac__session__list_ids
mcp__local-mac__session__get
```
