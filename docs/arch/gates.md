---
tags: gate framework, gate_check, PreToolUse, anti-hallucination, tool blocking, GateContext, imessage send, contacts search, mail delete, mail compose, gate enforcement, ALLOW, DENY, time window, session history, prompt tools, hooks/gates.py, gate_rules.yaml, external gates, adding a new gate
---
# Gate Framework

Gates are hard blocks on tool calls — they fire in `PreToolUse` before Claude can act, and deny irreversible or policy-violating operations unless a prerequisite is satisfied.

There are two kinds of gate, side by side in the same `GATES` registry:

- **Internal gates** — Python classes in `hooks/gates.py`, for tools that need DB access (task lifecycle, git commit traceability).
- **External gates** — declared in `~/.claude/gate_rules.yaml`, no Python code required, for MCP tools that live in other repos (e.g. the `local-mac` iMessage/Mail tools).

---

## How it works

```text
PreToolUse hook
  └── dispatcher._handle_pre_tool_use()
        └── session_graph run_gate chain
              └── GateCheckNode.__call__()
                    └── DefaultGatePolicy.check(tool_name, ctx) → GATES[tool_name].verify(ctx) → (deny, reason)
```

If `deny=True`, the hook returns a `permissionDecision: deny` response — Claude Code surfaces the `reason` inline and the tool call never executes. If `deny=False`, the tool proceeds.

**Fail-open:** any error inside the gate pipeline (missing config, DB error) allows the tool through — a broken gate should never be the reason a legitimate action fails. This applies to both the internal gate classes and the external YAML loader.

---

## Files

| File | Role |
| --- | --- |
| `hooks/gates.py` | `Gate` ABC, `GateContext`, all internal gate classes, external `gate_rules.yaml` loader, `GATES` registry |
| `langchain_learning/nodes/gate_check.py` | Builds `GateContext` from `SessionState`, dispatches to `DefaultGatePolicy` |
| `langchain_learning/retrievers.py` | `GatePolicy` protocol + `DefaultGatePolicy` (thin wrapper over `GATES`) |
| `~/.claude/gate_rules.yaml` | External gate declarations — no Python change needed to add one |

---

## Worked example: sending an iMessage

This is the flagship case for why gates exist — the send only goes through if you actually looked the contact up first, and the *right* contact.

```yaml
# ~/.claude/gate_rules.yaml
gates:
  - tool: imessage__send
    prereq: contacts__search
    name_arg: name        # contacts__search(name="Alice") → "Alice" must be in the prompt
    window_s: 120
```

What happens on a real call:

```text
1. contacts__search(name="Alice")  → logged into session_tools
2. imessage__send(recipient="+91...", message="hola")
     → gate_check finds contacts__search in the last 120s
     → name_arg_check: name="alice" found in current/previous prompt text? yes
     → ALLOW → message sent
```

If step 1 never happened, or you'd searched for a different name than the one in your prompt, the gate denies:

```text
Blocked: imessage__send — contacts__search was called for 'Alice' but that name does
not appear in the current or previous prompt. Search for the intended recipient first.
```

This is verified end-to-end against real log output (`claude_hooks.sqlite`), not just tested in isolation — see [External gates](#external-gates-gate_rulesyaml) below for the current entry list.

---

## Worked example: deleting an email

A simpler shape — pure prereq, no name matching, since there's nothing to match against (you're deleting, not addressing):

```yaml
  - tool: mail__delete
    prereq: mail__read
    window_s: 120
```

```text
1. mail__read(...)      → logged into session_tools
2. mail__delete(message_ids=[...])
     → gate_check finds mail__read within the last 120s → ALLOW → deleted
```

No `mail__read` in the last 120 seconds → denied with "Blocked: mail__delete requires mail__read within the last 120s. Call mail__read first, then retry." Simple, but it's what stops an email getting deleted on a hallucinated or stale read.

---

## `GateContext`

Built once per tool call from `SessionState` and passed to every gate's `verify()`:

| Field | Type | Description |
| --- | --- | --- |
| `tool_name` | `str` | Short tool name (MCP prefix stripped, or `"Bash"`) |
| `tool_input` | `dict` | Raw tool arguments |
| `current_calls` | `list[ToolCall]` | Tool calls made **this prompt only** |
| `session_tools` | `OrderedDict` | Tool calls keyed by prompt_id, spanning **the whole session** |
| `session_prompt_ids` | `list[str]` | Ordered prompt IDs this session |
| `prompt_id` | `str` | Current prompt ID |
| `prompt_text` | `str` | Current prompt text |
| `recent_prompt_texts` | `list[str]` | Current + previous prompt text, current first |

Helpers: `ctx.called_recently(tool, window_s)`, `ctx.called_this_session(tool)`, `ctx.prev_tools()` (reverse-chronological, spans the **whole session** via `session_tools` — not just `current_calls`).

> **Gotcha:** `gate_check`'s debug log line shows `current=[...]` from `current_calls`, which is scoped to only the current prompt. A prereq check reading `current=[]` in that debug line does **not** mean the prereq was missing — the actual prereq lookback (`ctx.prev_tools()`) reads `session_tools`, which spans every prior prompt in the session within the configured window. Don't conflate the two when debugging a gate.

---

## External gates (`gate_rules.yaml`)

Schema per entry:

```yaml
gates:
  - tool: <tool_name>       # required — short name as it appears in the hook event
    prereq: <prereq_tool>   # required — tool that must have run recently
    window_s: 120           # optional — staleness window in seconds, default 120
    name_arg: name          # optional — key in the PREREQ tool's input that must be
                            #   non-empty AND appear as a substring in the prompt
    input_arg: to           # optional — key in the GATED tool's own input that must
                            #   appear as a substring in the prompt
```

`name_arg` and `input_arg` are mutually exclusive in practice: use `name_arg` when the prereq is a lookup (`contacts__search(name="Alice")`), use `input_arg` when the relevant value is on the gated call itself (`mail__compose(to="alice@example.com")`). `input_arg` runs before the prereq scan — fails fast.

At startup, `_load_external_gates()` reads this file and builds one dynamically-generated `Gate` subclass per entry, merged into `GATES` — no Python class per external tool. Adding a new external gate is a YAML edit, not a code change.

**Current entries** (validated end-to-end against real log output):

- `imessage__send` — prereq `contacts__search`, `name_arg=name`
- `mail__compose` — prereq `contacts__search`, `input_arg=to`
- `mail__delete` — prereq `mail__read`

---

## Internal gates

These need database access (task lifecycle, commit traceability), so they stay as Python classes rather than YAML.

### `GitCommitGate` — `Bash`

**Requires:** if the Bash command contains a `git commit` (any form, including `git -C <path> commit`) or `git_local.sh`, a `task:<id>` pattern must appear somewhere in the command string.

**Why:** every commit must reference a task for traceability. Without this, Claude can silently commit without a task ID and the audit trail breaks.

```bash
# Denied
git commit -m "fix: something"

# Allowed
git -C /path commit -m "$(cat <<'EOF'
fix: something

task:12168f99
EOF
)"
```

Non-commit Bash calls (`git status`, `git log`, `git diff`, etc.) pass through immediately.

### `GitCommitMcpGate` — `git__commit`

**Requires:** the `task_id` parameter must be non-empty. Same intent as `GitCommitGate`, enforced at the typed-param level for the MCP commit tool (in `claude_for_mac_local`) instead of regex on a Bash string.

### `JiraHierarchyGate` — `tasks__create`

**Requires:** `issue_type='story'` or `'task'` must have `parent_id` set, and the parent's `issue_type` must be `epic`. `issue_type='subtask'` must have a parent of type `bug`, `story`, or `task`. `epic` needs no parent — it's the standalone/root type.

| issue_type | parent required | valid parent types |
| --- | --- | --- |
| `epic` | no | — |
| `story` / `task` | yes | `epic` |
| `bug` | yes | `epic` |
| `subtask` | yes | `story`, `task`, `bug` |
| `feedback` | yes | any finished task (via `tasks__create_feedback`, not this gate) |

### `TaskSetActiveGate` — `tasks__set_active`

**Requires:** the transition to `active` (in the checkpoint, not the DB — see below) must be valid per `is_valid_transition()`.

### `TaskUpdateGate` — `tasks__update`

**Requires:** any `status=` change goes through `is_valid_transition()`. Valid DB statuses: `open`, `done`, `abandoned`, `blocked` — **`active` is not a DB status**, it only exists in the LangGraph checkpoint (`active_task_id`). Transitions: `open → {done, blocked}`, `blocked → open`, any status `→ abandoned`.

### `TaskFinishGate` — `tasks__finish`

**Requires:** the same `is_valid_transition()` check, enforcing `→ done`.

All three task-status gates share `is_valid_transition()` in `src/tools/tasks.py` — there is no separate review-gate system. (An earlier review-state workflow — `review_runs`, a `TaskDoneGate` requiring review sign-off — was removed; task completion is a direct status transition today, with retrospection handled by the `/task-introspection` skill instead of a blocking gate.)

---

## Adding a new gate

**External tool (no DB access needed):** add an entry to `~/.claude/gate_rules.yaml`. No code change, no restart-and-redeploy cycle beyond the hook server picking up the file on its next load.

**Internal tool (needs DB access):**

```python
class MyToolGate(Gate):
    tool_name = "my_tool_name"  # short name, MCP prefix stripped

    def verify(self, ctx: GateContext) -> tuple[bool, str]:
        if <condition not met>:
            return True, "Blocked: <reason>. Do <X> first."
        return False, ""
```

Register it in `GATES` (or let it merge automatically if only the YAML loader touches it), add to `_FAIL_CLOSED_TOOLS` in `dispatcher.py` if the tool is irreversible and must deny on gate error rather than fail open, and write tests in `tests/test_gates.py` — at minimum: denied without prereq, allowed with prereq.

---

← [Architecture](../ARCHITECTURE.md) · [Task Framework](task_framework.md) · [Databases](databases.md)
