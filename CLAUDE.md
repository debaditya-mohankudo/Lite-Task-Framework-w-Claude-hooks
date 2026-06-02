# claude-hooks

Claude Code hooks + LangChain pipeline + MCP server for memory and session tracking.

## Architecture

```
Claude Code event
       │
       ├── UserPromptSubmit ──► hooks/memory_loader_lc.py
       │                              │
       │                        LCEL Pipeline (in-process, no HTTP)
       │                              │
       │                    ┌─────────▼──────────┐
       │                    │  DomainClassifier  │  detect domains from prompt
       │                    └─────────┬──────────┘
       │                              │
       │                    ┌─────────▼──────────────────────┐
       │                    │      RunnableParallel           │
       │                    │  ├── SQLiteMemoryRetriever      │  score MEMORY.sqlite
       │                    │  └── ToolHintsRetriever (BM25)  │  score tool_hints.sqlite
       │                    └─────────┬──────────────────────┘
       │                              │
       │                    ┌─────────▼──────────┐
       │                    │   format_output    │  → additionalSystemPrompt
       │                    └────────────────────┘
       │
       ├── PreToolUse ───────► hooks/pre_tool_use_lc.py
       │                              │
       │                        gates.py  →  allow / deny tool call
       │                        tool_usage_logger_lc.py  →  log to sessions.db
       │
       └── Stop ────────────► hooks/stop_hook_lc.py
                                      │
                                 aggregate keywords/domains → sessions.db


MCP Server (stdio)
       │
       mcp_server.py  ──  FastMCP "claude-hooks"
              │
              ├── memory__*   →  src/tools/memory.py  →  MEMORY.sqlite
              └── session__*  →  src/tools/session.py →  sessions.db
```

## Key Files

| Path | Purpose |
|---|---|
| `mcp_server.py` | FastMCP server entry point — registers all tools |
| `hooks/memory_loader_lc.py` | UserPromptSubmit hook — runs LCEL pipeline, injects context |
| `hooks/pre_tool_use_lc.py` | PreToolUse hook — gate check + tool logging |
| `hooks/stop_hook_lc.py` | Stop hook — persists session keywords/domains |
| `hooks/gates.py` | Allow/deny rules for tool use |
| `langchain_learning/pipeline.py` | LCEL pipeline: DomainClassifier → parallel retrievers → output |
| `langchain_learning/session_graph.py` | LangGraph StateGraph: load_memories → classify → score_tools → persist |
| `langchain_learning/domain_classifier.py` | Keyword-based domain detection |
| `langchain_learning/memory_retriever.py` | SQLiteMemoryRetriever (BaseRetriever) |
| `langchain_learning/tool_hints_retriever.py` | BM25 + domain-scoped ToolHintsRetriever |
| `src/tools/memory.py` | MCP memory tool handlers (CRUD on MEMORY.sqlite) |
| `src/tools/session.py` | MCP session tool handlers (CRUD on sessions.db) |

## Databases

| File | Contents |
|---|---|
| `~/.claude/MEMORY.sqlite` | Long-term memories (type, domain, priority, tags, body) |
| `~/.claude/sessions.db` | Session state + summaries (keywords, domains, tasks, turn) |
| `~/.claude/tool_hints.sqlite` | MCP tool usage frequency + keyword hints |

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

## MCP Config

Registered in the global `~/.claude/settings.json` under `mcpServers.claude-hooks`:

```json
"claude-hooks": {
  "command": "uv",
  "args": ["run", "python", "mcp_server.py"],
  "cwd": "/Users/debaditya/workspace/claude-hooks"
}
```
