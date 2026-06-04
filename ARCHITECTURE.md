# claude-hooks Architecture

> This document describes the system as built — the decisions made, why they were made, and the constraints that shaped the design.

---

## Overview

`claude-hooks` is a Python system that intercepts all four Claude Code hook events and runs a **LangGraph StateGraph pipeline** in response. Its responsibilities are:

1. **Memory injection** — score and inject relevant memories from `MEMORY.sqlite` into every prompt
2. **Tool hint surfacing** — retrieve relevant MCP tools based on prompt intent and domain
3. **Session context** — inject prior session summaries that match current keywords
4. **Anti-hallucination gating** — hard-block irreversible MCP tool calls (iMessage, mail) unless a prerequisite tool actually ran this prompt
5. **Tool usage tracking** — accumulate latency and keyword signals per MCP tool for future retrieval

---

## The Fundamental Constraint

Claude Code spawns a **separate Python subprocess** for each hook event. There is no shared in-process memory between hook invocations. This is the central architectural constraint everything else flows from:

```
UserPromptSubmit  →  python3 hooks/memory_loader_lc.py   (subprocess A, exits)
PreToolUse        →  python3 hooks/pre_tool_use_lc.py    (subprocess B, exits)
PostToolUse       →  python3 hooks/tool_usage_logger_lc.py (subprocess C, exits)
Stop              →  python3 hooks/stop_hook_lc.py        (subprocess D, exits)
```

A module-level singleton in subprocess A is gone by the time subprocess B runs. The only thing that bridges them is **a file on disk**.

---

## State Architecture

### SqliteSaver checkpoint as the shared bus

`SessionState` (a LangGraph `TypedDict`) is persisted to a `SqliteSaver` checkpoint DB (`~/.claude/sessions.db`) keyed by `session_id` (the LangGraph `thread_id`). Every hook entry point:

1. Reads the existing checkpoint for that `session_id`
2. Merges only event-specific inputs on top (never overwrites the whole state)
3. Runs its node chain
4. LangGraph writes the updated state back to the checkpoint

This means the checkpoint is the **IPC channel** between all four hook subprocesses. It is the effective singleton — it survives all four subprocess boundaries.

**Design rule:** If hook B needs to know what hook A did, A writes to `SessionState` and B reads from `SessionState`. A second database is never used as a signal channel between hooks.

### SessionState fields

```python
class SessionState(TypedDict):
    # Event routing
    event_type: str          # UserPromptSubmit | PreToolUse | PostToolUse | Stop

    # Prompt inputs
    prompt: str
    cwd: str
    session_id: str

    # Memory pipeline outputs
    memories: list[dict]
    session_context: str
    session_context_ids: list[int]
    keywords: list[str]
    domains: list[str]
    tool_hints: list[dict]
    skip_tools: bool

    # Classifier internals
    classifier_config: dict
    classifier_scores: dict
    matched_keywords: list[str]

    # Session tracking
    turn: int
    current_state: str

    # Tool event inputs
    tool_name: str
    tool_input: dict
    prompt_id: str
    prompt_tools: list[str]  # tools called this prompt (reset each UserPromptSubmit)

    # Gate outputs
    gate_denied: bool
    gate_reason: str

    # Logging
    duration_ms: float
    tool_use_id: str
```

### The blank-state anti-pattern

Early versions called `{**_blank_state(), ...event_inputs}` on every graph invocation, which silently overwrote the checkpoint. The fix: each entry point calls `graph.get_state(config)` first, then merges only the event-specific fields on top. The checkpoint supplies everything else.

---

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
  │                        load_memories
  │                          │
  │                        load_session_context
  │                          │
  │                        load_classifier_config
  │                          │
  │                        cwd_domain_detect
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
  │                        set_prompt_id ──► END
  │                          (resets prompt_tools=[])
  │
  ├── PreToolUse ──────► gate_check ──► END
  │
  ├── PostToolUse ─────► log_tool_usage ──► END
  │
  └── Stop ────────────► finalize_session
                           │
                         persist_session ──► END
```

### Node design principles

- Each node is a **callable class** (`class FooNode: def __call__(self, state) -> dict`)
- Nodes read only from `state`, return only a partial dict of fields they modify
- All nodes log entry via `_node_log.entry()` to iCloud SQLite for observability
- Cross-cutting timing (`→ node_name`, `← node_name Xms`) is applied at graph build time via `wrap(name, fn)` — instrumentation stays out of node files entirely
- `NODE_REGISTRY` + `get_node()` factory keeps `build_session_graph()` as pure wiring

---

## The UserPromptSubmit Pipeline

### Domain classification

The domain classifier (`domain_classifier.json`) assigns the prompt to one or more domains (e.g., `macos`, `vault`, `astrology`, `market-intel`). It runs in two stages:

1. **keyword_score** — direct keyword hits from `KEYWORD_SIGNALS` per domain
2. **combination_score** — bigram/trigram bonus signals (e.g., `{what, is}` → vault, to handle intent words that are stopwords for keyword scoring)
3. **cwd_domain_detect** — deterministic domain from the working directory path map (always wins if CWD matches)
4. **memory_domain_signal** — soft signal from the top-3 already-injected memories' domains
5. **apply_threshold** — filters scores; sets `skip_tools=True` if no domain passes

### Memory retrieval

`load_memories` scores all rows in `MEMORY.sqlite` against prompt keywords (BM25-style intersection). Memories have a `priority` field — lower number = more likely to inject regardless of score.

### Session context

`load_session_context` scores `session_summaries` rows by keyword overlap (tags weighted 3×, body 1×) and injects the top-2 as a `## Session context` block. This surfaces relevant prior conversation context without needing vector search.

### Tool hints

`score_tools` retrieves top-5 MCP tool hints from `tool_hints.sqlite` via BM25 keyword intersection, boosted by domain match. Skipped entirely when `skip_tools=True` (no domain detected). Weekly TF-IDF refresh (`hooks/refresh_tool_hints.py`) rewrites each tool's keyword column from accumulated `recent_prompts`.

### Output

`memory_loader_lc.py` assembles the state outputs into `additionalSystemPrompt`:

```
## Injected memories
...scored memories...

## Suggested tools
...top MCP tool hints...

## Session context
...top-2 session summaries...
```

---

## Anti-Hallucination Gate (PreToolUse)

### The problem

Claude can recall "I already looked up the contact" from in-context conversation history and proceed to call `imessage__send` without actually searching contacts in the current prompt. Memory injection is not enforcement — the model can ignore it.

### The solution

`gate_check` reads `prompt_tools` from the LangGraph checkpoint — a `list[str]` of tool short-names that actually executed this prompt, written by `log_tool_usage` (PostToolUse) and reset to `[]` by `set_prompt_id` each new UserPromptSubmit.

```python
prompt_tools = set(state.get("prompt_tools") or [])
deny, reason = _gate_check(
    tool_name,
    lambda prereq: prereq in prompt_tools,
    tool_input,
)
```

Gate rules live in `hooks/gates.py` as a `_GATES` registry of frozen dataclasses. Current gates:

| Tool | Prerequisite |
|------|-------------|
| `imessage__send` | `contacts__search` |
| `mail__compose` | `contacts__search` |

The gate is **prompt-scoped only** — a `contacts__search` from a previous prompt does not satisfy the gate. The checkpoint's `prompt_tools` is reset to `[]` at the start of each new `UserPromptSubmit` turn.

Tool names are normalized (MCP prefix stripped) inside `log_tool_usage` so both the gate and the log see the same short name regardless of call path.

---

## Tool Usage Tracking (PostToolUse)

`log_tool_usage` does two things:

1. **Upserts** a row in `tool_hints.sqlite` — increments count, updates rolling average latency, appends the prompt text to `recent_prompts` (last 10)
2. **Appends** the short tool name to `prompt_tools` in LangGraph checkpoint state

It does **not** write to `sessions.db`. The checkpoint is the only record of which tools ran this prompt.

---

## Databases

| File | Purpose | Writer |
|------|---------|--------|
| `~/.claude/MEMORY.sqlite` | Long-term memories (type, domain, priority, tags, body) | MCP `memory__add` tool |
| `~/.claude/sessions.db` | Session rows + `session_summaries` | `persist_session` node (Stop chain only) |
| `~/.claude/sessions.db` (checkpoint tables) | LangGraph SqliteSaver checkpoint — cross-hook state | LangGraph internal |
| `~/Library/.../tool_hints.sqlite` | MCP tool usage frequency + keyword hints (iCloud) | `log_tool_usage` node |
| `~/Library/.../claude_hooks.sqlite` | All hook observability logs (iCloud) | `sqlite_log_handler.py` |

`sessions.db` holds both the session summary rows (written by `persist_session`) and the LangGraph checkpoint tables (written by `SqliteSaver`). These are separate tables in the same file.

---

## MCP Tools

Memory and session tools are hosted inside the `local-mac` MCP server (`~/workspace/claude_for_mac_local/src/dispatcher.py`), not in this repo. They were migrated out of a standalone `claude-hooks` MCP server to fix a VS Code startup failure where the stdio MCP never registered correctly.

The dispatcher uses an **isolated loader** (`_load_hooks_module`) that temporarily swaps `sys.path` to avoid namespace collisions between two repos both using `from src.X import Y`.

Tool domains:
- `memory__*` → `src/tools/memory.py` → `MEMORY.sqlite`
- `session__*` → `src/tools/session.py` → `sessions.db`

Use `session__list_ids` (not `session__list`) when only session identification is needed — `session__list` serializes full blob fields and hits the 157KB tool result buffer limit.

---

## Observability

All hook runs emit structured logs to `claude_hooks.sqlite` in iCloud via `sqlite_log_handler.py`. Every node calls `_node_log.entry()` at entry with its name and relevant state fields.

**Read logs via MCP, never `sqlite3` directly:**
```
mcp__local-mac__memory__read_compact   — compact summary for a session
mcp__local-mac__session__list_ids      — all sessions (minimal fields)
mcp__local-mac__session__get           — full session detail
```

---

## Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| State persistence | SqliteSaver checkpoint | Only mechanism that survives all four subprocess boundaries |
| Cross-hook signaling | `SessionState` fields only | DB-as-IPC was eliminated — gate and log share `prompt_tools` via checkpoint |
| Gate scope | Prompt-scoped only | Session-window fallback was a loophole: Claude's in-context memory can fake prior tool calls |
| Node design | Callable class per file | Testable, composable, no circular imports; mirrors ACME POC patterns |
| Instrumentation | `wrap()` at graph build time | Cross-cutting timing without touching node files |
| MCP hosting | Inside `local-mac` server | Eliminates VS Code stdio registration failures; cross-repo import isolated via `_load_hooks_module` |
| Tool hints refresh | Weekly TF-IDF cron | Accumulate signal over time, not per-prompt; IDE context bleed (XML tags) stripped before tokenizing |
| Domain classifier | Keyword + bigram signals | Handles intent words (`what is`) that are stopwords for keyword scoring |

---

## What This Is Not

- **Not a daemon.** Each hook is a subprocess that exits. A long-lived process (daemon architecture with HTTP/socket) would enable true in-process singletons but adds reliability surface area.
- **Not vector search.** Memory and session retrieval use BM25 keyword scoring. Precise tags on memory rows are the primary retrieval lever — adding `birthday`, `date-of-birth`, `kuna` to a contact note's frontmatter makes it rank first.
- **Not a LangServe server** (anymore). An HTTP fallback path was prototyped (`serve_pipeline.py` + `pipeline_client.py`) but the in-process graph is the production path.
