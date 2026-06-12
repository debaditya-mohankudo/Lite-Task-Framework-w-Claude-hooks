# claude-hooks

## Task Tracking

Use `tasks__*` MCP tools (via `local-mac`) for all multi-step work — instead of TodoWrite. Tasks persist across sessions, surface automatically when referenced, and build a development trail.

- `tasks__create(title, body?)` — start a new task; returns `task:<id>`
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

## Observability

All hook logs write to `claude_hooks.sqlite` in iCloud via `sqlite_log_handler.py`.

**Always use the MCP tool to read logs — never query the DB directly with sqlite3:**

```text
mcp__claude-hooks__hooks__read_logs_sqlite
mcp__local-mac__memory__read_compact
mcp__local-mac__session__list_ids
mcp__local-mac__session__get
```
