# Quality & Tests Wiki

**Full documentation:** Vault → `Documentation/Tools/QUALITY_WIKI.md`

## Quick Start

```bash
# Python tests (all suites)
cd ~/workspace/claude-hooks
uv run python -m pytest tests/ -v

# Specific suite
uv run python -m pytest tests/test_session_tools.py -v
```

## Latest Run — 2026-06-02

**160 passed, 16 failed** (uv run python -m pytest tests/ -q)

16 failures are pre-existing in `test_langchain_session_graph` and `test_langchain_memory_loader_lc` — both patch `MEMORY_DB` which was removed from those modules.

| Suite | Tests | Passed | Notes |
|---|---|---|---|
| test_session_tools | 28 | 28 | ✓ New — MCP session tool handlers |
| test_langchain_session_graph | 27 | 11 | 16 failing (MEMORY_DB patch broken) |
| test_langchain_pipeline | 24 | 24 | ✓ |
| test_langchain_domain_classifier | 21 | 21 | ✓ |
| test_hooks_lc | 20 | 20 | ✓ |
| test_langchain_tool_hints_retriever | 19 | 19 | ✓ |
| test_langchain_memory_loader_lc | 16 | 14 | 2 failing (MEMORY_DB patch broken) |
| test_langchain_hook_runnable | 11 | 11 | ✓ (e2e vs real DBs) |
| test_langchain_memory_retriever | 10 | 10 | ✓ |

### Session Tool Coverage (test_session_tools)

| Class | Tests | What it covers |
|---|---|---|
| TestHandleListIds | 6 | Minimal-field listing, field exclusion, ordering, empty/missing DB |
| TestHandleList | 3 | Full session listing, list_all delegation |
| TestHandleGet | 3 | Lookup by ID, unknown ID, missing DB |
| TestHandleKeywords | 2 | Keyword extraction, unknown session |
| TestHandleTasks | 2 | Empty tasks, unknown session |
| TestHandleDelete | 2 | Delete existing, delete nonexistent |
| TestSummaries | 5 | Save/retrieve, ordering, delete, no-tags |
| TestHandleSearch | 5 | Tag match, tag weighting, no match, scoped, top_k |

## Previous Run — 2026-04-14

| Suite | Result |
|---|---|
| test_connection_pool | ✓ PASSED |
| test_connection_pool_fast | ✓ PASSED |
| test_parallel_dispatch | ✓ PASSED |
| test_agent_responses | ~ SKIPPED |
| MCP dispatch (10 categories) | ✓ 10/10 |
| Shell/Docker | ~ SKIPPED (Docker down) |

## Previous Run — 2026-04-12

### Shell Tests (test_tools.sh)

| Metric | Value |
| --- | --- |
| Total Runtime | 2m 34.56s |
| Tests Passed | 73 |
| Tests Failed | 0 |
| Success Rate | 100% |

**Breakdown:**

- Storage: 2/2 ✓
- Notes: 1/1 ✓
- Reminders: 1/1 ✓
- Calendar: 1/1 ✓
- Mail: 1/1 ✓
- SSH guardrails: 23/23 ✓
- Finder: 7/7 ✓
- Network: 5/5 ✓
- Process: 6/6 ✓
- Screen Recording: 4/4 ✓

### Parallel Dispatch Test

| Test | Latency | Status |
| --- | --- | --- |
| Single tool (baseline) | 77ms | ✓ |
| 2 concurrent tools | 285ms | ✓ |
| 3 concurrent tools | 196ms | ✓ |

See the vault wiki for comprehensive test coverage, guardrails, and how to add new tests.
