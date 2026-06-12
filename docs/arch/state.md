# State Architecture

## The Fundamental Constraint

Claude Code spawns a **separate Python subprocess** for each hook event. There is no shared in-process memory between hook invocations. This is the central architectural constraint everything else flows from:

```
UserPromptSubmit  →  python3 hooks/dispatcher.py   (subprocess A, exits)
PreToolUse        →  python3 hooks/dispatcher.py   (subprocess B, exits)
PostToolUse       →  python3 hooks/dispatcher.py   (subprocess C, exits)
Stop              →  python3 hooks/dispatcher.py   (subprocess D, exits)
```

A module-level singleton in subprocess A is gone by the time subprocess B runs. The only thing that bridges them is **a file on disk**.

---

## SqliteSaver checkpoint as the shared bus

`SessionState` (a LangGraph `TypedDict`) is persisted to a `SqliteSaver` checkpoint DB (`~/.claude/langgraph_checkpoints.db`) keyed by `session_id` (the LangGraph `thread_id`). Every hook entry point:

1. Reads the existing checkpoint for that `session_id`
2. Merges only event-specific inputs on top (never overwrites the whole state)
3. Runs its node chain
4. LangGraph writes the updated state back to the checkpoint

This means the checkpoint is the **IPC channel** between all four hook subprocesses. It is the effective singleton — it survives all four subprocess boundaries.

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
    task_stack: list[str]            # LIFO stack of suspended task IDs; push on switch, pop to restore
    mid_task_decisions: list[str]    # explicit design decisions logged during active task
    related_tasks: list[dict]        # top-3 done tasks by cosine similarity via TurboVec (.tasks_embeddings.tvim)

    # --- explicit project override (set by /switch-project) ---
    project_domain_override: str     # when set, cwd_domain_detect uses this instead of cwd_domain_map

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
