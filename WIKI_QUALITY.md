# Quality & Tests Wiki

**Full documentation:** Vault → `Documentation/Tools/QUALITY_WIKI.md`

## Quick Start

```bash
# Python tests (all suites)
cd ~/workspace/claude_for_mac_local
uv run pytest tests/ -v

# Specific suite
uv run pytest tests/test_langchain_pipeline.py -v

# MCP dispatch categories (Swift)
~/workspace/claude_for_mac_local/local-mac-mcp/Tests/test_dispatch_categories.sh

# Shell/Docker tests (requires Docker running)
bash ~/workspace/claude_for_mac_local/tests/test_tools.sh
```

## Latest Run — 2026-06-02

**253 passed, 0 failed** (uv run pytest tests/ -q)

| Suite | Tests | Result |
|---|---|---|
| test_langchain_domain_classifier | 30 | ✓ PASSED |
| test_langchain_session_graph | 27 | ✓ PASSED |
| test_send_gate | 26 | ✓ PASSED |
| test_langchain_pipeline | 24 | ✓ PASSED |
| test_astrology | 24 | ✓ PASSED |
| test_langchain_tool_hints_retriever | 19 | ✓ PASSED |
| test_memory_tools | 16 | ✓ PASSED |
| test_langchain_memory_loader_lc | 16 | ✓ PASSED |
| test_vault_tools | 13 | ✓ PASSED |
| test_langchain_hook_runnable | 11 | ✓ PASSED (7 e2e vs real DBs) |
| test_langchain_memory_retriever | 10 | ✓ PASSED |
| test_tool_hints | 8 | ✓ PASSED |
| test_session_tools | 8 | ✓ PASSED |
| test_scorer_protocols | 6 | ✓ PASSED |
| test_swift_bridge | 5 | ✓ PASSED |
| test_dispatcher | 5 | ✓ PASSED |
| test_config | 5 | ✓ PASSED |

### LangChain Components (C1–C6)

| Component | File | Tests |
|---|---|---|
| C1 — SQLiteMemoryRetriever | test_langchain_memory_retriever | 10 |
| C2 — SessionGraph | test_langchain_session_graph | 27 |
| C3 — DomainClassifier | test_langchain_domain_classifier | 30 |
| C4 — ToolHintsRetriever | test_langchain_tool_hints_retriever | 19 |
| C5 — LCEL Pipeline | test_langchain_pipeline | 24 |
| C5 hook — memory_loader_lc | test_langchain_memory_loader_lc | 16 |
| C6 — HookRunnable (e2e) | test_langchain_hook_runnable | 11 |

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
