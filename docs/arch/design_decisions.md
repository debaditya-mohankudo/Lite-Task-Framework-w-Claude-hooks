# Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| State persistence | SqliteSaver checkpoint | Only mechanism that survives all four subprocess boundaries |
| Cross-hook signaling | `SessionState` fields only | DB-as-IPC was eliminated — gate and log share `prompt_tools` via checkpoint |
| Gate scope | Time-scoped (120s window) | Session-window fallback was a loophole: Claude's in-context memory can fake prior tool calls |
| Send gates | `@prereq` decorator + `Gate` class per tool | Adding a gate = one class + one registry entry. Current gates: `imessage__send` → `contacts__search`; `mail__compose` → `contacts__search`; `mail__delete` → `mail__read` |
| iMessage name validation | Substring search in raw `prompt_text` | Classifier `keywords` are domain signal tokens, not recipient names — raw prompt text is the only reliable surface to check. Fail-open when `prompt_text` is empty. |
| Prompt audit trail | `session_prompt_ids` + `session_tools` in checkpoint | Full per-prompt tool history available for cross-turn analysis |
| Node design | Callable class per file | Testable, composable, no circular imports |
| Instrumentation | `wrap()` at graph build time | Cross-cutting timing without touching node files |
| MCP hosting | Inside `local-mac` server | Eliminates VS Code stdio registration failures; cross-repo import isolated via `_load_hooks_module` |
| Tool hints refresh | Weekly TF-IDF cron | Accumulate signal over time, not per-prompt; IDE context bleed (XML tags) stripped before tokenizing |
| Domain classifier | Keyword + bigram signals | Handles intent words (`what is`) that are stopwords for keyword scoring |
| Session summaries | Not injected into system prompt | Task injection provides sufficient context; session summaries were redundant |
| Task/UPS routing after `load_turn` | Inline lambda in `add_conditional_edges` | Two options considered: (1) `Command(goto=...)` returned from `load_turn` — node owns routing but `goto` is magic and couples node logic to graph topology; (2) inline lambda — routing stays in graph wiring where it belongs, one-liner, no standalone function needed. Lambda chosen. |
| Related past tasks retrieval | BM25 keyword overlap (not RAG/vector) | Corpus is small (60–200 done tasks). RAG adds embedding model dependency and index rebuild on every task completion. BM25 is fast, zero deps, deterministic. Signal quality is bounded by title specificity and corpus size — revisit RAG when corpus hits ~200 tasks and vocabulary divergence becomes the bottleneck. |

---

## What This Is Not

- **Not a daemon.** Each hook is a subprocess that exits. A long-lived process would enable true in-process singletons but adds reliability surface area.
- **Not vector search.** Memory retrieval uses BM25 keyword scoring. Precise tags on memory rows are the primary retrieval lever.
- **Not a LangServe server** (anymore). An HTTP fallback path was prototyped (`serve_pipeline.py` + `pipeline_client.py`) but the in-process graph is the production path.
