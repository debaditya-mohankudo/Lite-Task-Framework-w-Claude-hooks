# Quality & Tests Wiki

**Full documentation:** Vault → `Documentation/Tools/claude-hooks/QUALITY_WIKI.md`

## Quick Start

```bash
# Python tests (all suites)
cd ~/workspace/claude-hooks
uv run python -m pytest tests/ -v

# Specific suite
uv run python -m pytest tests/test_session_tools.py -v
```

## Latest Run — 2026-06-04

**236 passed, 0 failed** (uv run python -m pytest tests/ -q)

Overall coverage: **79%** (up from 71%)

| Suite | Tests | Passed | Notes |
|---|---|---|---|
| test_gates | 30 | 30 | ✓ New — gate policy, phone number checks, AddressBook lookup |
| test_memory_tools | 30 | 30 | ✓ New — MCP memory tool handlers (add/get/list/search/delete/tool_hints/read_compact) |
| test_session_tools | 28 | 28 | ✓ MCP session tool handlers |
| test_langchain_session_graph | 27 | 27 | ✓ |
| test_langchain_pipeline | 24 | 24 | ✓ |
| test_langchain_domain_classifier | 21 | 21 | ✓ |
| test_hooks_lc | 20 | 20 | ✓ |
| test_langchain_tool_hints_retriever | 19 | 19 | ✓ |
| test_langchain_memory_loader_lc | 16 | 16 | ✓ |
| test_langchain_memory_loader_e2e | 11 | 11 | ✓ |
| test_langchain_memory_retriever | 10 | 10 | ✓ |

### Coverage highlights

| File | Coverage |
|---|---|
| hooks/gates.py | 96% |
| src/tools/memory.py | 94% |
| langchain_learning/tool_hints_retriever.py | 96% |
| langchain_learning/memory_retriever.py | 97% |
| langchain_learning/gate_pipeline.py | 100% |

### New test suites added (2026-06-04)

**test_gates.py** — `hooks/gates.py` (security-critical gate policy)

| Area | Tests |
|---|---|
| Gate.is_satisfied (any/all logic) | 4 |
| Gate.deny_reason (custom + auto-generated) | 3 |
| GATES registry assertions | 4 |
| _is_phone_number | 5 |
| check() — ungated, denied, allowed | 6 |
| Secondary phone number check | 5 |
| _number_in_contacts (mocked AddressBook) | 3 |

**test_memory_tools.py** — `src/tools/memory.py` (MCP memory handlers)

| Handler | Tests |
|---|---|
| handle_add | 4 |
| handle_get | 2 |
| handle_list | 4 |
| handle_search | 5 |
| handle_list_domains | 3 |
| handle_delete | 2 |
| handle_tool_hints | 5 |
| handle_read_compact | 3 |

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
