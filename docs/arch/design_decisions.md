---
tags: design decisions, architecture decisions, SqliteSaver, synchronous PostToolUse, daemon thread revert, gate enforcement, blank state anti-pattern, session bus, checkpoint, fire-and-forget, tool usage tracking, hook server design
---
# Key Design Decisions

| Decision | Choice | Rationale |
| --- | --- | --- |
| State persistence | SqliteSaver (`~/.claude/langgraph_checkpoints.db`) | Durable across server reloads; keeps only the 2 most recently active sessions (evicts older threads at startup). MemorySaver was trialled briefly but reverted — durable disk state is required for the server restart workflow. |
| Cross-hook signaling | `SessionState` fields only | DB-as-IPC was eliminated — gate and log share `prompt_tools` via checkpoint |
| Gate scope | Time-scoped (120s window) | Session-window fallback was a loophole: Claude's in-context memory can fake prior tool calls |
| Send gates | Internal `Gate` classes (DB-needing tools) + external `gate_rules.yaml` (no-code tools) | Splitting by whether a gate needs DB access: task/git gates stay as Python classes; iMessage/Mail-style gates (`imessage__send`→`contacts__search`, `mail__compose`→`contacts__search`, `mail__delete`→`mail__read`) are declared in YAML with no Python class per tool. See [Gates](gates.md). |
| iMessage name validation | Substring search in raw `prompt_text` | Raw prompt text is the only reliable surface — domain `keywords` are signal tokens, not recipient names. Fail-open when `prompt_text` is empty. |
| Prompt audit trail | `session_prompt_ids` + `session_tools` in checkpoint | Full per-prompt tool history available for cross-turn analysis |
| Node design | Callable class per file | Testable, composable, no circular imports |
| Instrumentation | `wrap()` at graph build time | Cross-cutting timing without touching node files |
| MCP hosting | Inside `local-mac` server | Eliminates VS Code stdio registration failures; cross-repo import isolated via `_load_hooks_module` |
| Tool hints refresh | Weekly TF-IDF cron | Accumulate signal over time, not per-prompt; IDE context bleed (XML tags) stripped before tokenizing |
| Domain detection | Deterministic CWD match via `CWD_DOMAIN_MAP` in `src/config.py` | Removed probabilistic keyword/bigram classifier — deterministic is simpler, zero false positives, no config drift |
| Session summaries | Not injected into system prompt | Task injection provides sufficient context; session summaries were redundant |
| Server memory | SQLite + in-memory session cache (`~/.claude/server_memory.sqlite`) | Two-layer design: SQLite is the durable backing (rolling 1000-event window); in-memory cache is hydrated at server startup so restarts don't lose context. Writes are write-through. Events are a single table with a `type` discriminator (`prompt`\|`tool`\|`task`) — one `ORDER BY ts` gives the full interleaved timeline. Assistant turns are deliberately excluded; `server_session_id` tags rows by run but is not a lifecycle boundary — events from many runs coexist. Test sessions are skipped to avoid polluting the store. |
| Task context summarization | Re-enabled (task:b9dded3e, 2026-07-04), first-turn-of-activation gated | `summarize_task_context` (still `BareClaudeAgent`/`claude -p` subprocess) was removed 2026-06-22 for ~5s timeout risk, then re-enabled gated to run at most once per task activation (only when `task_context` has ≤1 prior entry) so the cost is bounded to a single occurrence per task rather than every turn. As of this check it has never actually crossed its 800-char invocation threshold in production — see [[summarize-task-context-never-actually-fired]]. |
| Task/UPS routing after `load_turn` | Inline lambda in `add_conditional_edges` | Routing stays in graph wiring where it belongs — `Command(goto=...)` from a node couples node logic to topology unnecessarily |
| Task status transitions | `handle_update()` is the only write path | `handle_update` runs `is_valid_transition()` — the single source of truth for the state machine (`open→{done,blocked}`, `blocked→open`, any→`abandoned`; `active` is a checkpoint concept, not a DB status). Nodes must never `UPDATE open_tasks SET status=...` directly; doing so silently bypasses the guard and breaks gate correctness. A separate review-gate stage (`review` status, `review_runs`) existed earlier and was removed — see [Task Framework](task_framework.md). |
| Related past tasks retrieval | TurboVec semantic search (`.tasks_embeddings.tvim`, Ollama `nomic-embed-text`) | BM25 missed vocabulary divergence — tasks using different words for the same concept didn't surface. Vector similarity handles synonyms and adjacent topics naturally. Index rebuilt at MCP startup; incremental upserts on create/finish/activate. |

---

## What This Is Not

- **Not a stateless subprocess.** The production path is a long-lived FastAPI server (launchd-managed daemon). The old architecture had each hook exit after each call; that model is gone.
- **Not vector search.** Memory retrieval uses BM25 keyword scoring. Precise tags on memory rows are the primary retrieval lever.
- **Not a LangServe server** (anymore). An HTTP fallback path was prototyped (`serve_pipeline.py` + `pipeline_client.py`) but the in-process graph is the production path.

---

← [Architecture](../ARCHITECTURE.md) · [Graph & Pipeline](graph_pipeline.md) · [Task Framework](task_framework.md) · [Gates](gates.md)
