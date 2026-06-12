# Gate Framework

Gates are hard blocks on tool calls — they fire in `PreToolUse` before Claude can act, and deny irreversible or policy-violating operations unless a prerequisite is satisfied.

---

## How it works

```text
PreToolUse hook
  └── dispatcher._handle_pre_tool_use()
        └── session_graph.run_gate()
              └── GateCheckNode.__call__()
                    └── gates.check(tool_name, ctx) → (deny, reason)
```

If `deny=True`, the hook returns a `permissionDecision: deny` response — Claude Code surfaces the `reason` inline and the tool call never executes.

**Fail-open:** any error inside the gate pipeline allows the tool through (except tools in `_FAIL_CLOSED_TOOLS`, which deny on error).

---

## Files

| File | Role |
| --- | --- |
| `hooks/gates.py` | Gate ABC, `@prereq` decorator, all gate classes, `GATES` registry |
| `langchain_learning/nodes/gate_check.py` | Builds `GateContext` from `SessionState`, dispatches to `gates.check()` |
| `hooks/dispatcher.py` | Routes `Bash` and `mcp__*` tool calls into `run_gate()` |

---

## Gate ABC

```python
class Gate(ABC):
    tool_name: str  # key used in GATES registry

    @abstractmethod
    def verify(self, ctx: GateContext) -> tuple[bool, str]:
        """Return (deny, reason). deny=True blocks the call."""
```

Logging is injected automatically via `__init_subclass__` — subclasses never call `_log` directly.

---

## GateContext

Built once per tool call from `SessionState` and passed to every gate:

| Field | Type | Description |
| --- | --- | --- |
| `tool_name` | `str` | Short tool name (MCP prefix stripped, or `"Bash"`) |
| `tool_input` | `dict` | Raw tool arguments |
| `current_calls` | `list[ToolCall]` | Tool calls this prompt (with timestamps) |
| `session_tools` | `OrderedDict` | Tool calls keyed by prompt_id, all prompts this session |
| `session_prompt_ids` | `list[str]` | Ordered prompt IDs this session |
| `prompt_id` | `str` | Current prompt ID |
| `prompt_text` | `str` | Current prompt text (lower-cased) |
| `recent_prompt_texts` | `list[str]` | Current + previous prompt text |

Helpers: `ctx.called_recently(tool, window_s)`, `ctx.called_this_session(tool)`, `ctx.prev_tools()`.

---

## `@prereq` decorator

Injects a time-bounded prerequisite check as `verify()` — no boilerplate needed:

```python
@prereq("contacts__search", window_s=120, name_arg="name")
class IMessageSendGate(Gate):
    tool_name = "imessage__send"
```

| Param | Effect |
| --- | --- |
| `tool` | Prereq tool that must have been called recently |
| `window_s` | Staleness window in seconds (default: 120) |
| `name_arg` | If set: prereq must have been called with a non-empty value for this key, **and** that value must appear as a substring in the current or previous prompt text |

The `name_arg` check prevents a stale or hallucinated contact lookup from satisfying the gate.

---

## Current gates

### `IMessageSendGate` — `imessage__send`

**Requires:** `contacts__search` called within 120s with a non-empty `name` arg, and that name must appear in the current or previous prompt.

**Why:** Prevents Claude from sending a message to the wrong person due to a hallucinated or stale contact lookup.

---

### `MailComposeGate` — `mail__compose`

**Requires:** `contacts__search` called within 120s.

**Why:** Ensures the recipient was explicitly looked up before composing a message.

---

### `MailDeleteGate` — `mail__delete`

**Requires:** `mail__read` called within 120s.

**Why:** Ensures the email was read and confirmed before deletion — prevents blind deletes.

---

### `GitCommitGate` — `Bash`

**Requires:** If the Bash command contains `git commit` or `git_local.sh`, a `task:<id>` pattern must appear somewhere in the command string.

**Why:** Every commit must reference an active task for traceability. Without this gate, Claude can silently commit without a task ID and the audit trail is broken.

Non-commit Bash calls pass through immediately.

```python
# Denied
git commit -m "fix: something"

# Allowed
git commit -m "fix: something\n\ntask:12168f99"
~/workspace/.../git_local.sh -y "fix: something\n\ntask:12168f99"
```

---

### `JiraHierarchyGate` — `tasks__create`

**Requires:** When `issue_type` is `story`, `task`, or `bug` — `parent_id` must be set and the parent's `issue_type` must be `epic`. When `issue_type` is `subtask` — `parent_id` must be set and the parent must be a `story`, `task`, or `bug`. Epics may not have a parent.

**Why:** Enforces the Jira hierarchy at tool-call time. Uses the `parent_id` column (indexed) for the lookup — no tag parsing. Fail-open on DB error, consistent with other gates.

| issue_type | parent required | valid parent types |
| --- | --- | --- |
| `epic` | no | — |
| `story` / `task` / `bug` | yes | `epic` |
| `subtask` | yes | `story`, `task`, `bug` |

---

## Adding a new gate

1. **Subclass `Gate`** in `hooks/gates.py`:

```python
class MyToolGate(Gate):
    tool_name = "my_tool_name"  # short name, MCP prefix stripped

    def verify(self, ctx: GateContext) -> tuple[bool, str]:
        if <condition not met>:
            return True, "Blocked: <reason>. Do <X> first."
        return False, ""
```

Or use `@prereq` if the check is just "tool X must have run recently":

```python
@prereq("some__prereq_tool", window_s=120)
class MyToolGate(Gate):
    tool_name = "my_tool_name"
```

1. **Register it** in `GATES`:

```python
GATES: dict[str, Gate] = {g.tool_name: g for g in [
    ...
    MyToolGate(),
]}
```

1. **For built-in tools** (non-MCP, e.g. `Bash`): add a handling branch in `dispatcher._handle_pre_tool_use()` alongside the existing `Bash` case if needed.

1. **Add to `_FAIL_CLOSED_TOOLS`** in `dispatcher.py` if the tool is irreversible and must deny on gate error (rather than fail-open).

1. **Write tests** in `tests/test_gates.py` — at minimum: denied without prereq, allowed with prereq, registered in `GATES`.

---

← [Architecture](../ARCHITECTURE.md) · [Graph & Pipeline](graph_pipeline.md) · [Databases](databases.md)
