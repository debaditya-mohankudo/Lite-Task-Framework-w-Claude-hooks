# Databases, MCP Tools & Observability

## Databases

| File | Purpose | Writer |
| --- | --- | --- |
| `~/.claude/MEMORY.sqlite` | Long-term memories (type, domain, priority, tags, body) | MCP `memory__add` tool |
| `~/.claude/proj_tasks.db` | Task rows + turn event log | MCP `tasks__*` tools |
| `~/.claude/langgraph_checkpoints.db` | LangGraph SqliteSaver checkpoint — cross-hook state | LangGraph internal |
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

### Two logger implementations, one table

| Module | Used by | Write strategy | Logger prefix |
| --- | --- | --- | --- |
| `src/logger.py` | All LangGraph nodes | **Buffered** — accumulates in `_buffer[]`, flushed atomically by `flush_logs()` at hook exit | `lc.<module>` |
| `hooks/sqlite_log_handler.py` | Hook entry-point scripts | **Per-record** — writes immediately on each `emit()` | bare name |

`src/logger.py` is the primary logger for all node code. The buffered approach means a single `executemany` commit per hook invocation rather than one connection-open per log line.

**Flush requirement:** `flush_logs()` must be called at hook exit. If a hook crashes before that call, the buffer is discarded silently — by design, logging must never crash a hook.

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
