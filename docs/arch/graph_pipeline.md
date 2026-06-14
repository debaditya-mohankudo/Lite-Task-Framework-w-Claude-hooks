# Graph & Pipeline Architecture

## Graph Architecture

A single `StateGraph` in `langchain_learning/session_graph.py` handles all four hook events. The `event_type` field routes to one of four node chains via a conditional edge at `START`.

```text
START
  │
  ▼
route_event  (conditional edge on event_type)
  │
  ├── UserPromptSubmit ──► load_turn
  │                          │
  │               ┌──────────┴──────────────────┐
  │               │ task active?                 │ no task
  │               ▼                              │
  │          load_active_task                    │
  │          ╔══╦══╦══╗ fan-out                  │
  │          ║  ║  ║  ║                          │
  │   history╝  ║  ╚code                         │
  │    (parallel)║   (parallel)                  │
  │              ╚related ◄──────────────────────┘
  │         ╔══╦══╦══╗  fan-out (parallel)
  │         ║  ║  ╚══╝
  │    domain╝  ║  score_tools
  │    detect   ╚memories
  │         ╚══╦══╦══╝  fan-in
  │            ▼
  │        set_prompt_id
  │            │
  │        log_task_events ──► END
  │
  ├── PreToolUse ──────► gate_check ──► END
  │
  ├── PostToolUse ─────► log_tool_usage ──► (activate_task | deactivate_task | decision_task | END)
  │
  └── Stop ────────────► noop ──► END
```

### Node design principles

- Each node is a **callable class** (`class FooNode: def __call__(self, state) -> dict`)
- Nodes read only from `state`, return only a partial dict of fields they modify
- All nodes log entry via `_node_log.entry()` to iCloud SQLite for observability
- Cross-cutting timing (`→ node_name`, `← node_name Xms`) is applied at graph build time via `wrap(name, fn)` — instrumentation stays out of node files entirely
- `NODE_REGISTRY` + `get_node()` factory keeps `build_session_graph()` as pure wiring

### Phase observability

Every log line includes `phase=parallel|sequential`. The single source of truth is `_PARALLEL_NODES` frozenset in `langchain_learning/nodes/_node_log.py` — update it when the graph topology changes.

```text
[cwd_domain_detect] phase=parallel event=user_prompt_submit session=8789f089 turn=5
← load_memories phase=parallel session=8789f089 14.2ms
UPS phase=done session=8789f089 elapsed_ms=18
```

### Fan-out / fan-in

LangGraph runs multiple edges from one node in parallel via `ThreadPoolExecutor`. Fan-in waits for all branches before the next super-step, and the checkpointer writes one checkpoint after the entire fan-in — not once per parallel node. In production this is MemorySaver (in-process dict); in tests, SqliteSaver against a temp DB.

**Active-task fan-out (tier 1):** `load_active_task` → `[load_task_history ∥ load_task_code ∥ load_related_tasks]`

**Domain/memory fan-out (tier 2):** `load_related_tasks` (and the two tier-1 nodes) each fan out to → `[cwd_domain_detect ∥ load_memories ∥ score_tools]`, all fan-in at `set_prompt_id`.

Parallel nodes must not read state keys written by other parallel nodes in the same tier. `load_memories` and `score_tools` infer domain directly from `cwd` (via `_src_cfg.cwd_domain_map`) rather than reading `state["domains"]`, which `cwd_domain_detect` writes.

---

## The UserPromptSubmit Pipeline

### Without an active task

When no task is active, the task nodes no-op:

- `load_active_task` — returns `{}` immediately
- `load_task_history` — returns `task_context: []`
- `load_task_code` — returns `task_rag_chunks: []`
- `load_related_tasks` — returns `related_tasks: []`

Context is built purely from domain + memory signals.

### Domain detection

Domain is determined deterministically — no scoring, no classification pipeline.

**`cwd_domain_detect`** matches the CWD path against `CWD_DOMAIN_MAP` in `src/config.py` (substring match, first key wins).

Valid domains are declared in `VALID_DOMAINS` in `src/config.py`. The map is loaded fresh on every hook invocation so edits take effect without restart.

### Relevant code (semantic RAG)

`load_task_code` runs when a task is active. It embeds the active task title via Ollama (`nomic-embed-text`) and searches `.code_embeddings.tvim` using TurboVec — the same index and embed model used by the `/explain` skill. Returns top-3 symbols (class/function/section) by cosine similarity, with `module`, `file`, `line`, and `kind` fields. Injected as `## Relevant code`.

This gives current-state grounding: rather than showing what commits touched the task, it shows what code is semantically closest to the task goal right now. Falls back silently if the index is missing or Ollama is unavailable.

### Related past tasks

`load_related_tasks` runs when a task is active. It calls `handle_neighbors(active_task_id)` which embeds the task title + body via Ollama (`nomic-embed-text`) and queries `.tasks_embeddings.tvim` (TurboVec) for the top semantically similar tasks. Results are filtered to `status=done` and capped at 3. Injected as `## Related past tasks`.

The index lives at the repo root alongside `.code_embeddings.tvim` and is rebuilt automatically on MCP server startup if missing. Incremental upserts run on every `tasks__create`, `tasks__finish`, and `tasks__set_active` call so the index stays current without full rebuilds.

Signal quality depends on corpus size. Novel tasks with no prior done neighbours will return empty.

### Memory retrieval

`load_memories` uses a two-query split:

- **Always-include** — rows with `priority=1` or matching the current domain are fetched directly and injected without scoring
- **Scored batch** — remaining rows (capped at 200) are BM25-scored via token set intersection against prompt keywords; top scorers are injected up to a soft limit

Memories have a `priority` field — lower number = higher precedence. `priority=1` rows are always injected regardless of relevance score.

### Tool hints

`score_tools` retrieves top-5 MCP tool hints from `tool_hints.sqlite` via BM25 keyword intersection, boosted by domain match. A weekly cron job (`scripts/refresh_tool_hints.py`) rewrites each tool's keyword column from accumulated `recent_prompts` via TF-IDF.

### System prompt output

`dispatcher.py` assembles state outputs into `additionalSystemPrompt`:

```text
## Active task          (if task active)
## Task memories        (if task active)
## Task history         (if task active)
## Relevant code        (if task active and index exists)
## Related past tasks   (if task active and related done tasks found)
## Injected memories
## Suggested tools
## Turn state
```

See [system_prompt.md](system_prompt.md) for section details.

---

## Anti-Hallucination Gate (PreToolUse)

### The problem

Claude can recall "I already looked up the contact" from in-context conversation history and proceed to call `imessage__send` without actually searching contacts in the current prompt. Memory injection is not enforcement — the model can ignore it.

### The solution

`gate_check` reads `prompt_tools`, `session_tools`, and `prompt` (raw text) from the LangGraph checkpoint. It builds a `GateContext` with the full call history and dispatches to the matching `Gate` subclass in `hooks/gates.py`.

Gate rules are `Gate` subclasses decorated with `@prereq`. Each gate checks that a prerequisite tool actually fired within a time window — using `ctx.prev_tools()` which spans both the current prompt and session history. Adding a new gate = one new class + one entry in `GATES`. Nothing else changes.

Current gates:

| Tool | Prerequisite | Window | Extra check |
| ---- | ------------ | ------ | ----------- |
| `imessage__send` | `contacts__search` (non-empty `name` arg) | 120s | searched name must appear in current prompt text |
| `mail__compose` | `contacts__search` | 120s | — |
| `mail__delete` | `mail__read` | 120s | — |

The check is time-scoped, not prompt-scoped — a prerequisite from earlier in the same session satisfies the gate as long as it falls within the window. Each gate emits its own `[tool_name] ALLOW/DENY` log line via the `@prereq` decorator.

For `imessage__send`, the gate also verifies that the `name` value passed to `contacts__search` is a substring of the current prompt text (case-insensitive). This prevents a stale or hallucinated contact lookup from satisfying the gate. The check is skipped when `prompt_text` is empty (fail-open for backward compatibility).

Tool names are normalized (MCP prefix stripped) inside `log_tool_usage` so both the gate and the log see the same short name regardless of call path.

### hooks__checkpoint_query

`mcp__local-mac__hooks__checkpoint_query` reads the latest LangGraph checkpoint and returns the full state snapshot — including `prompt_id`, `session_id`, `domains`, `keywords`, injected memories, and tool hints.

> **Note:** In the persistent server model (2026-06-14+), state lives in MemorySaver (in-process). `hooks__checkpoint_query` reads `langgraph_checkpoints.db` which is no longer written to in production. Use `curl http://127.0.0.1:8766/session` for live session info instead.

This is the correct way to inspect live state mid-conversation when `prompt_id`/`session_id` are needed as explicit tool arguments.

---

## Tool Usage Tracking (PostToolUse)

### Synchronous pipeline

`_handle_post_tool_use` in `dispatcher.py` runs the pipeline synchronously and returns after it completes. Fire-and-forget (daemon thread) was tried but reverted: the hook is a short-lived subprocess, so daemon threads are killed at process exit — tools with large results (e.g. `mail__read`) never finish writing to the checkpoint, breaking gate prereq checks in subsequent calls.

### `log_tool_usage` node

Does three things in one node (previously split across two):

1. **Upserts** a row in `tool_hints.sqlite` — increments count, updates rolling average latency, appends the prompt text to `recent_prompts` (last 10), seeds `keywords` from tool name tokens + domain if empty
2. **Appends** tool name to `task_events.tools` in `tasks.db` for the current `prompt_id` row
3. **Updates** `prompt_tools` and `session_tools` in LangGraph checkpoint state

Steps 1 and 2 write to different SQLite databases and run concurrently via `ThreadPoolExecutor(max_workers=2)`.

`update_tool_keywords` was merged into this node — keyword seeding now happens in the same SQLite connection as the upsert, eliminating a second DB round-trip and LangGraph checkpoint write.

The checkpoint is the only record of which tools ran this prompt.

---

← [Architecture](../ARCHITECTURE.md) · [State](state.md) · [System Prompt](system_prompt.md)
