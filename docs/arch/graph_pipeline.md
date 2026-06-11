# Graph & Pipeline Architecture

## Graph Architecture

A single `StateGraph` in `langchain_learning/session_graph.py` handles all four hook events. The `event_type` field routes to one of four node chains via a conditional edge at `START`.

```
START
  │
  ▼
route_event  (conditional edge on event_type)
  │
  ├── UserPromptSubmit ──► load_turn
  │                          │
  │                        load_active_task
  │                          │
  │                        load_task_history
  │                          │
  │                        load_task_code
  │                          │
  │                        load_related_tasks
  │                          │
  │                        cwd_domain_detect
  │                          │
  │                        load_memories
  │                          │
  │                        keyword_score
  │                          │
  │                        combination_score
  │                          │
  │                        memory_domain_signal
  │                          │
  │                        apply_threshold
  │                          │
  │                   ┌──────┴──────┐
  │               skip_tools      score_tools
  │                   └──────┬──────┘
  │                          │
  │                        set_prompt_id
  │                          │
  │                        log_task_events ──► END
  │
  ├── PreToolUse ──────► gate_check ──► END
  │
  ├── PostToolUse ─────► log_tool_usage ──► update_tool_keywords ──► END
  │
  └── Stop ────────────► noop ──► END
```

### Node design principles

- Each node is a **callable class** (`class FooNode: def __call__(self, state) -> dict`)
- Nodes read only from `state`, return only a partial dict of fields they modify
- All nodes log entry via `_node_log.entry()` to iCloud SQLite for observability
- Cross-cutting timing (`→ node_name`, `← node_name Xms`) is applied at graph build time via `wrap(name, fn)` — instrumentation stays out of node files entirely
- `NODE_REGISTRY` + `get_node()` factory keeps `build_session_graph()` as pure wiring

---

## The UserPromptSubmit Pipeline

### Without an active task

When no task is active, the task nodes no-op:
- `load_active_task` — returns `{}` immediately
- `load_task_history` — returns `task_context: []`
- `load_task_code` — returns `task_rag_chunks: []`
- `load_related_tasks` — returns `related_tasks: []`

Context is built purely from domain + memory signals.

### Domain classification

The domain classifier assigns the prompt to one or more domains (e.g., `macos`, `vault`, `astrology`, `market-intel`). It runs in stages:

1. **cwd_domain_detect** — deterministic domain from the working directory path map (always wins if CWD matches)
2. **keyword_score** — direct keyword hits from `KEYWORD_SIGNALS` per domain
3. **combination_score** — bigram/trigram bonus signals (e.g., `{what, is}` → vault)
4. **memory_domain_signal** — soft signal from the top-3 already-injected memories' domains
5. **apply_threshold** — filters scores; sets `skip_tools=True` if no domain passes

### Relevant code (semantic RAG)

`load_task_code` runs when a task is active. It embeds the active task title via Ollama (`nomic-embed-text`) and searches `.code_embeddings.tvim` using TurboVec — the same index and embed model used by the `/explain` skill. Returns top-3 symbols (class/function/section) by cosine similarity, with `module`, `file`, `line`, and `kind` fields. Injected as `## Relevant code`.

This gives current-state grounding: rather than showing what commits touched the task, it shows what code is semantically closest to the task goal right now. Falls back silently if the index is missing or Ollama is unavailable.

### Related past tasks

`load_related_tasks` runs when a task is active. It tokenises the active task title and scores all `done` rows in `proj_tasks.db` by BM25 keyword overlap against each row's `title + tags + body`. Top-3 by score are injected as `## Related past tasks`. Useful for surfacing prior art — similar work already completed in previous sessions.

Signal quality depends on corpus size and title specificity. Novel concepts with no prior done tasks will return empty. Commit SHAs and related task IDs are both logged per turn for quality evaluation.

### Memory retrieval

`load_memories` scores all rows in `MEMORY.sqlite` against prompt keywords (BM25-style intersection). Memories have a `priority` field — lower number = more likely to inject regardless of score.

### Tool hints

`score_tools` retrieves top-5 MCP tool hints from `tool_hints.sqlite` via BM25 keyword intersection, boosted by domain match. Skipped entirely when `skip_tools=True` (no domain detected). A weekly cron job (`scripts/refresh_tool_hints.py`) rewrites each tool's keyword column from accumulated `recent_prompts` via TF-IDF.

### System prompt output

`dispatcher.py` assembles state outputs into `additionalSystemPrompt`:

```
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

`mcp__local-mac__hooks__checkpoint_query` reads the latest LangGraph checkpoint from `langgraph_checkpoints.db` and returns the full state snapshot — including `prompt_id`, `session_id`, `domains`, `keywords`, injected memories, and tool hints.

This is the correct way to inspect live state mid-conversation when `prompt_id`/`session_id` are needed as explicit tool arguments.

---

## Tool Usage Tracking (PostToolUse)

`log_tool_usage` does two things:

1. **Upserts** a row in `tool_hints.sqlite` — increments count, updates rolling average latency, appends the prompt text to `recent_prompts` (last 10)
2. **Appends** the short tool name to `prompt_tools` in LangGraph checkpoint state

The checkpoint is the only record of which tools ran this prompt.
