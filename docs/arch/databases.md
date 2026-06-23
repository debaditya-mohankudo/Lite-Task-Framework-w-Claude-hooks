---
tags: databases, MEMORY.sqlite, sessions.db, tool_hints.sqlite, proj_tasks.db, langgraph_checkpoints.db, claude_hooks.sqlite, iCloud, SqliteSaver, MCP tools, observability, logging, sqlite_log_handler, memory__add, memory__get, memory__list, memory__delete, tasks__create, tasks__update, hooks__read_logs_sqlite, checkpoint_query
---
# Databases, MCP Tools & Observability

## Databases

| File | Purpose | Writer |
| --- | --- | --- |
| `~/.claude/MEMORY.sqlite` | Long-term memories (type, domain, priority, tags, body) | MCP `memory__add` tool |
| `~/.claude/proj_tasks.db` | Task rows + turn event log | MCP `tasks__*` tools |
| `~/.claude/langgraph_checkpoints.db` | LangGraph `SqliteSaver` checkpoint — graph state persists across reloads and restarts; trimmed to 2 sessions on startup | FastAPI server (SqliteSaver) |
| `~/.claude/server_memory.sqlite` | ServerMemory durable event store — cross-session recency (prompts, MCP tools, activated tasks); 1000-event rolling window; hydrated into in-memory cache at startup | `hooks/server_memory.py` (write-through) |
| `~/Library/.../tool_hints.sqlite` | MCP tool usage frequency + keyword hints (iCloud) | `log_tool_usage` node |
| `~/Library/.../claude_hooks.sqlite` | All hook observability logs (iCloud) | `sqlite_log_handler.py` |

CWD→domain mapping is declared in `CWD_DOMAIN_MAP` in `src/config.py` — not an external file. Keys are CWD substrings, values are domain names from `VALID_DOMAINS`. First match wins.

---

## MCP Tools

Memory and task tools are hosted inside the `local-mac` MCP server (`~/workspace/claude_for_mac_local/src/dispatcher.py`), not in this repo. They were migrated out of a standalone `claude-hooks` MCP server to fix a VS Code startup failure where the stdio MCP never registered correctly.

The dispatcher uses an **isolated loader** (`_load_hooks_module`) that temporarily swaps `sys.path` to avoid namespace collisions between two repos both using `from src.X import Y`.

Tool domains:

- `memory__*` → `src/tools/memory.py` → `MEMORY.sqlite`
- `tasks__*` → `src/tools/tasks.py` → `proj_tasks.db`

---

## Observability

All hook runs emit structured logs to `claude_hooks.sqlite` in iCloud. Every log record lands in the `hook_logs` table: `(id, logger, level, message, ts)`.

### Logger

| Module | Used by | Write strategy | Logger prefix |
| --- | --- | --- | --- |
| `src/logger.py` | All LangGraph nodes and hook endpoints | **Immediate** — opens a fresh SQLite connection on each `emit()`, no buffering | `lc.<module>` |

`src/logger.py` is the sole logger for all node and server code. The FastAPI server is long-lived so per-emit writes are safe — no `flush_logs()` call needed. `hooks/sqlite_log_handler.py` is retired.

**Auto-prune:** `sqlite_log_handler.py` caps `hook_logs` at 50K rows, pruning to 40K when exceeded.

### Node-level instrumentation

Two layers, both in `langchain_learning/nodes/_node_log.py`:

- **`entry(node, state, **extra)`** — called at the top of every node's `__call__`; logs `event_type`, `session_id[:8]`, `turn`, and any node-specific extras at INFO level
- **`wrap(name, fn)`** — applied at graph build time in `build_session_graph()`; emits `→ node_name` before and `← node_name Xms` after at DEBUG level

Instrumentation is applied at the graph wiring layer (`wrap()`) rather than inside node files — so nodes stay clean and timing is never forgotten when a new node is added.

### Reading logs

**Always use MCP — never query `claude_hooks.sqlite` directly with `sqlite3`:**

```text
mcp__claude-hooks__hooks__read_logs_sqlite  — query hook logs
mcp__local-mac__memory__read_compact       — compact summary for a session
```

---

← [Architecture](../ARCHITECTURE.md) · [Task Framework](task_framework.md) · [Graph & Pipeline](graph_pipeline.md)
