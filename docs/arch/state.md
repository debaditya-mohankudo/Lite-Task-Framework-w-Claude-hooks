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

```python
class SessionState(TypedDict):
    # Event routing
    event_type: str          # user_prompt_submit | pre_tool_use | post_tool_use | stop

    # Prompt inputs
    prompt: str
    cwd: str
    session_id: str
    turn: int

    # Memory pipeline outputs
    memories: list[dict]
    keywords: list[str]
    domains: list[str]
    tool_hints: list[dict]
    skip_tools: bool

    # Task framework
    active_task_id: str
    active_task_title: str
    task_memories: list[dict]
    task_context: list[dict]
    task_commits: list[dict]
    task_stack: list[str]

    # Classifier internals
    classifier_scores: dict
    matched_keywords: list[str]

    # Session tracking
    current_state: str

    # Tool event inputs
    tool_name: str
    tool_input: dict
    prompt_id: str
    prompt_tools: list[str]         # tools called this prompt (reset each UserPromptSubmit)
    session_prompt_ids: list[str]   # all prompt_ids seen this session (append-only)
    session_tools: OrderedDict      # {prompt_id: [tool_names]} — full session audit trail

    # Gate outputs
    gate_denied: bool
    gate_reason: str

    # Logging
    duration_ms: float
    tool_result: dict
```

---

## The blank-state anti-pattern

Early versions called `{**_blank_state(), ...event_inputs}` on every graph invocation, which silently overwrote the checkpoint. The fix: each entry point calls `graph.get_state(config)` first, then merges only the event-specific fields on top. The checkpoint supplies everything else.
