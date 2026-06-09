# Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| State persistence | SqliteSaver checkpoint | Only mechanism that survives all four subprocess boundaries |
| Cross-hook signaling | `SessionState` fields only | DB-as-IPC was eliminated — gate and log share `prompt_tools` via checkpoint |
| Gate scope | Time-scoped (120s window) | Session-window fallback was a loophole: Claude's in-context memory can fake prior tool calls |
| iMessage gate | `contacts__search` prerequisite | Contact lookup prevents hallucinated numbers |
| Prompt audit trail | `session_prompt_ids` + `session_tools` in checkpoint | Full per-prompt tool history available for cross-turn analysis |
| Node design | Callable class per file | Testable, composable, no circular imports |
| Instrumentation | `wrap()` at graph build time | Cross-cutting timing without touching node files |
| MCP hosting | Inside `local-mac` server | Eliminates VS Code stdio registration failures; cross-repo import isolated via `_load_hooks_module` |
| Tool hints refresh | Weekly TF-IDF cron | Accumulate signal over time, not per-prompt; IDE context bleed (XML tags) stripped before tokenizing |
| Domain classifier | Keyword + bigram signals | Handles intent words (`what is`) that are stopwords for keyword scoring |
| Session summaries | Not injected into system prompt | Task injection provides sufficient context; session summaries were redundant |

---

## What This Is Not

- **Not a daemon.** Each hook is a subprocess that exits. A long-lived process would enable true in-process singletons but adds reliability surface area.
- **Not vector search.** Memory retrieval uses BM25 keyword scoring. Precise tags on memory rows are the primary retrieval lever.
- **Not a LangServe server** (anymore). An HTTP fallback path was prototyped (`serve_pipeline.py` + `pipeline_client.py`) but the in-process graph is the production path.
