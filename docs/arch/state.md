---
tags: state architecture, SessionState, LangGraph state, TypedDict, state fields, prompt, session_id, prompt_id, cwd, domains, keywords, memories, task_context, active_task_id, task_rag_chunks, related_tasks, related_commits, prompt_tools, session_tools, SqliteSaver, checkpoint, immutable state, state schema
---
# State Architecture

## The Architecture (as of 2026-06-23)

Claude Code hooks are delivered to a **persistent FastAPI server** (`hooks/server.py`) via `hooks/client.sh` (curl). The server uses a `SqliteSaver` (`~/.claude/langgraph_checkpoints.db`) keyed by `session_id` — durable across server restarts and `--reload` cycles.

```
UserPromptSubmit  →  curl POST localhost:8766/hook/UserPromptSubmit  ─┐
PreToolUse        →  curl POST localhost:8766/hook/PreToolUse         │  FastAPI server
PostToolUse       →  curl POST localhost:8766/hook/PostToolUse        │  (persistent)
Stop              →  curl POST localhost:8766/hook/Stop              ─┘
```

State lives in a `SqliteSaver` checkpoint keyed by `session_id` — durable across server restarts. On `SessionEnd` (not Stop), the server evicts the session checkpoint.

> **Historical note:** The original model spawned a separate subprocess per hook and used `SqliteSaver` as the IPC channel between them (required because subprocesses can't share in-memory state). This caused daemon thread death (PostToolUse threads killed on subprocess exit) and ~600ms latency from subprocess cold starts. The persistent server eliminates both — SqliteSaver is retained for durability, but runs in-process so it's fast.

---

## SqliteSaver as the session bus

`SessionState` (a LangGraph `TypedDict`) is checkpointed in `SqliteSaver` keyed by `session_id` (the LangGraph `thread_id`). Every hook request:

1. Reads the checkpoint for that `session_id` from `~/.claude/langgraph_checkpoints.db`
2. Merges only event-specific inputs on top (never overwrites the whole state)
3. Runs its node chain
4. LangGraph writes the updated state back to SqliteSaver

**Design rule:** If hook B needs to know what hook A did, A writes to `SessionState` and B reads from `SessionState`. A second database is never used as a signal channel between hooks.

---

## SessionState fields

```python title="langchain_learning/session_state.py"
class SessionState(TypedDict):
    # --- routing ---
    event_type: str          # "user_prompt_submit" | "pre_tool_use" | "post_tool_use" | "stop"

    # --- common ---
    prompt: str
    cwd: str
    session_id: str
    turn: int

    # --- UserPromptSubmit outputs ---
    memories: list[dict]
    domains: list[str]
    keywords: list[str]
    tool_hints: list[dict]
    active_task_id: str              # set via task_activate branch; flows through session via checkpoint
    active_task_title: str
    task_memories: list[dict]        # memories scored against task tags+title at activation
    task_context: list[dict]         # prior turn events for active task (current session only)
    task_rag_chunks: list[dict]      # top-3 code modules from TurboVec semantic search over .code_embeddings.tvim
    task_body: str                   # body of the active task — injected into system prompt
    task_context_summary: str        # compressed summary via claude -p (haiku); replaces raw lists when present
    task_stack: list[str]            # LIFO stack of suspended task IDs; push on switch, pop to restore
    mid_task_decisions: list[str]    # explicit design decisions logged during active task
    related_tasks: list[dict]        # top-3 done tasks by cosine similarity via TurboVec (.tasks_embeddings.tvim)
    related_commits: list[dict]      # top-3 diff hunks by cosine similarity via TurboVec (.diff_embeddings.tvim)
    active_parent_task_id: str       # parent epic id, if the active task has one
    active_parent_task_title: str    # parent epic title for context injection

    # --- stop chain ---
    current_state: str               # "prompt" | "stop"

    # --- prompt tracking ---
    prompt_id: str
    prompt_tools: list[str]                      # tool short-names called this prompt (reset each UPS)
    session_prompt_ids: list[str]                # ordered list of all prompt_ids in this session
    session_tools: OrderedDict[str, list[dict]]  # prompt_id → [{"tool", "tool_input", "ts"}]
    session_prompt_texts: dict[str, str]         # prompt_id → prompt text; used by gates

    # --- PreToolUse / PostToolUse inputs ---
    tool_name: str
    tool_input: dict

    # --- PreToolUse outputs ---
    gate_denied: bool
    gate_reason: str

    # --- PostToolUse inputs ---
    duration_ms: float
    tool_result: dict
```

---

## The blank-state anti-pattern

Early versions called `{**_blank_state(), ...event_inputs}` on every graph invocation, which silently overwrote the checkpoint. The fix: each entry point calls `graph.get_state(config)` first, then merges only the event-specific fields on top. The checkpoint supplies everything else.

---

← [Architecture](../ARCHITECTURE.md) · [Graph & Pipeline](graph_pipeline.md) · [Task Framework](task_framework.md)
