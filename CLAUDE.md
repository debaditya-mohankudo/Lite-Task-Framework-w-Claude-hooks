# claude-hooks

## Task Tracking

Use `tasks__*` MCP tools (via `local-mac`) for all multi-step work — instead of TodoWrite. Tasks persist across sessions, surface automatically when referenced, and build a development trail.

- `tasks__create(title, body?)` — start a new task; returns `task:<id>`
- `tasks__update(id, status?, body?)` — mark `wip` when starting, `done` when finished
- `tasks__list()` — see all open/wip tasks
- `tasks__history(id)` — full turn-by-turn development log for a task

Reference a task as `task:<id>` in any prompt to pin it — it will be injected into context and logged automatically at session end.

Use TodoWrite only for ephemeral within-session tracking (e.g. sub-steps of a single prompt). For anything spanning multiple turns or sessions, use `tasks__*`.

Claude Code hooks + LangChain pipeline for memory and session tracking. MCP tools (memory/session) are hosted inside the `local-mac` MCP server.

## Architecture

All four Claude Code hook events are handled by a single **LangGraph StateGraph** (`session_graph.py`). Each event routes to its own node chain; a unified `SessionState` TypedDict flows through.

```
Claude Code event
       │
       ├── UserPromptSubmit ──► hooks/memory_loader_lc.py
       │                              │
       │                        LangGraph (in-process, no HTTP)
       │                              │
       │                    START → route_event
       │                              │
       │                    user_prompt_submit branch:
       │                      load_turn → load_memories → load_session_context
       │                      → load_classifier_config → cwd_domain_detect
       │                      → keyword_score → combination_score
       │                      → memory_domain_signal → apply_threshold
       │                      → score_tools (optional) → set_prompt_id → END
       │                              │
       │                         → additionalSystemPrompt (injected memories +
       │                           tool hints + session context)
       │
       ├── PreToolUse ───────► hooks/pre_tool_use_lc.py
       │                              │
       │                    pre_tool_use branch:
       │                      gate_check → END
       │                        (gates.py — allow / deny tool call)
       │                      tool_usage_logger_lc.py — log to sessions.db
       │
       ├── PostToolUse ──────► hooks/tool_usage_logger_lc.py
       │                              │
       │                    post_tool_use branch:
       │                      log_tool_usage → END
       │
       └── Stop ────────────► hooks/stop_hook_lc.py
                                      │
                            stop branch:
                              finalize_session → persist_session → END
                                (sole DB writer for session data)


MCP Tools (via local-mac)
       │
       ~/workspace/claude_for_mac_local/src/dispatcher.py
              │
              ├── memory__*   →  src/tools/memory.py  →  MEMORY.sqlite
              └── session__*  →  src/tools/session.py →  sessions.db
```

## Key Files

| Path | Purpose |
|---|---|
| `hooks/memory_loader_lc.py` | UserPromptSubmit hook — runs LangGraph pipeline, injects context via additionalSystemPrompt |
| `hooks/pre_tool_use_lc.py` | PreToolUse hook — gate check + tool logging |
| `hooks/stop_hook_lc.py` | Stop hook — persists session keywords/domains |
| `hooks/gates.py` | Allow/deny rules for tool use |
| `langchain_learning/session_graph.py` | LangGraph StateGraph — unified event graph, all four hook event branches |
| `langchain_learning/session_state.py` | `SessionState` TypedDict — shared state flowing through all nodes |
| `langchain_learning/nodes/registry.py` | `NODE_REGISTRY` + `get_node()` factory — one class per node file |
| `langchain_learning/nodes/load_turn.py` | Reads current turn counter from sessions.db |
| `langchain_learning/nodes/load_memories.py` | Scores MEMORY.sqlite rows against prompt keywords |
| `langchain_learning/nodes/load_session_context.py` | Top-2 session summaries by keyword score (tags×3 + body×1) |
| `langchain_learning/nodes/load_classifier_config.py` | Loads `domain_classifier.json` from iCloud into state |
| `langchain_learning/nodes/cwd_domain_detect.py` | Deterministic domain from CWD map; CWD always from state, never `os.getcwd()` |
| `langchain_learning/nodes/keyword_score.py` | Scores strong/weak keyword signals from prompt text |
| `langchain_learning/nodes/combination_score.py` | Adds bigram/trigram combination bonuses on top of keyword scores |
| `langchain_learning/nodes/memory_domain_signal.py` | Soft domain signal from top-3 injected memories |
| `langchain_learning/nodes/apply_threshold.py` | Filters classifier_scores by threshold; sets `skip_tools` if none pass |
| `langchain_learning/nodes/score_tools.py` | Retrieves top-5 tool hints by domain + keyword overlap; skipped when `skip_tools=True` |
| `langchain_learning/nodes/set_prompt_id.py` | Generates prompt_id UUID, writes to DB — only mid-turn DB write |
| `langchain_learning/nodes/gate_check.py` | Enforces send-gate policy; sets `gate_denied` + `gate_reason` |
| `langchain_learning/nodes/log_tool_usage.py` | Upserts tool hint and records prompt_tool_call (PostToolUse) |
| `langchain_learning/nodes/finalize_session.py` | Filters stopwords, sets stop state snapshot; no DB write |
| `langchain_learning/nodes/persist_session.py` | Sole DB writer for session data (Stop chain only) |
| `langchain_learning/nodes/noop.py` | Fallback no-op for unrecognised event types |
| `src/tools/memory.py` | MCP memory tool handlers (CRUD on MEMORY.sqlite) |
| `src/tools/session.py` | MCP session tool handlers (CRUD on sessions.db) |

## Databases

| File | Contents |
|---|---|
| `~/.claude/MEMORY.sqlite` | Long-term memories (type, domain, priority, tags, body) |
| `~/.claude/sessions.db` | Session state + summaries (keywords, domains, tasks, turn); top-2 summaries injected per prompt |
| `~/Library/Mobile Documents/com~apple~CloudDocs/Databases/tool_hints.sqlite` | MCP tool usage frequency + keyword hints (iCloud) |

## MCP Tools

**memory__*** — add / get / search / list / delete / read_compact / tool_hints  
**session__*** — list_ids / list / get / keywords / tasks / save_summary / get_summaries / search / delete

Use `session__list_ids` (not `session__list`) when you only need to identify sessions — `session__list` returns full blobs and hits the 157KB tool result limit.

## Running Tests

```bash
uv run python -m pytest tests/ -v
uv run python -m pytest tests/test_session_tools.py -v   # session tools only
```

See `WIKI_QUALITY.md` for latest run results. Full doc: Vault → `Documentation/Tools/claude-hooks/QUALITY_WIKI.md`

## Observability

All hook logs (domain classification, memory retrieval, tool hints, session injection) write to `claude_hooks.sqlite` in iCloud via `sqlite_log_handler.py`.

**Always use the MCP tool to read logs — never query the DB directly with sqlite3:**

```text
mcp__local-mac__memory__read_compact
mcp__local-mac__session__list_ids
mcp__local-mac__session__get
```

Memory and session tools are now hosted inside the `local-mac` MCP server (registered as `memory__*` and `session__*` domains). The standalone `claude-hooks` MCP server is no longer registered.

## MCP Config

Memory and session tools are served via `local-mac` MCP — see `~/workspace/claude_for_mac_local/src/dispatcher.py` for the `memory` and `session` domain entries.
