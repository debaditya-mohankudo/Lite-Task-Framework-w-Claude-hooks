# Task Feedback Loop

The feedback loop is the mechanism by which work done on a task feeds back into future tasks as injected context. It closes the gap between "I did this once" and "I know this when I need it."

## The Loop

```
Activate task
     │
     ▼
Context injected automatically (each UPS turn)
  • related past tasks  — cosine similarity via TurboVec
  • related commits     — diff RAG hunks
  • scored memories     — BM25 tag+body overlap
     │
     ▼
Implement with full prior-art context
     │
     ▼
tasks__finish()
     │
     ▼
PostToolUse hook fires → DeactivateTaskNode
  • Injects retrospective prompt via additionalContext
  • Clears active task from checkpoint
     │
     ▼
Retrospective prompt asks Claude to extract up to 2 atomic memories:
  • Decision made — and why
  • Constraint discovered
  • Pattern that worked (or failed)
     │
     ▼
Claude calls memory__add_batch with correct domain + tags
     │
     ▼
Memories stored in MEMORY.sqlite
     │
     ▼
Next related task activated → memories score high → injected
     │
     └──────────────────────────────────────────────┘
                        loop closed
```

## What Gets Captured

The retrospective prompt (injected as `additionalContext` on `tasks__finish`) asks for **non-obvious** findings only:

| Category | Example |
|---|---|
| Decision made | "Chose BM25 over embeddings — no GPU, latency < 50ms required" |
| Constraint discovered | "MCP tool responses are always wrapped in `content[0].text` JSON" |
| Pattern that worked | "Narrow tags to tool-specific terms to stop over-firing" |
| Pattern that failed | "Broad tags like `memory, add` match every claude-hooks prompt" |

Obvious things (what the code does, that tests passed) are not worth saving — the code and git history already say that.

## Memory Quality Rules

Each atomic memory saved through the loop should follow:

```
body: <rule or fact — one sentence>

Why: <the reason this was non-obvious or hard-won>
How to apply: <when this should change future behavior>
```

Tags should be **natural-language keywords** that match future prompts mentioning the same concept — not slugs or internal IDs.

## Components

| Component | File | Role |
|---|---|---|
| `DeactivateTaskNode` | `langchain_learning/nodes/deactivate_task.py` | Writes retrospective `additionalContext` to `pending_hook_output` on `tasks__finish` |
| `run_post_tool()` | `langchain_learning/session_graph.py` | Returns `pending_hook_output` from final graph state to dispatcher |
| `_handle_post_tool_use()` | `hooks/dispatcher.py` | Passes hook output back to Claude Code as the PostToolUse response |
| `SessionState.pending_hook_output` | `langchain_learning/session_state.py` | Transient field — set by node, cleared after each PTU turn |
| `score_memories()` + `LoadMemoriesNode` | `langchain_learning/nodes/` | Forward injection — surfaces saved memories on the next related task |

## Forward vs Backward

| Direction | When | Mechanism |
|---|---|---|
| **Forward** (inject) | Each UPS turn while task is active | BM25 scoring over MEMORY.sqlite + TurboVec RAG |
| **Backward** (capture) | On `tasks__finish` | PostToolUse retrospective → `memory__add_batch` |

Forward injection is automatic. Backward capture is prompted — Claude decides what's worth saving based on the retrospective template. Poor memories (vague, obvious, no tags) won't surface; precise memories with good tags compound over time.

## Tuning

- **Too many memories firing**: narrow tags on the memory to tool-specific terms
- **Relevant memory not surfacing**: add natural-language keywords to tags that match how future prompts describe the concept
- **Retrospective prompt too noisy**: edit `_RETROSPECTIVE_TEMPLATE` in `deactivate_task.py`
- **No retrospective wanted on clear_active**: by design — only `tasks__finish` triggers it; `tasks__clear_active` (mid-session task switch) does not
