---
name: explain
description: Explain a symbol or concept in the current project — combines code graph lookup, file read, and task history for full context. Use when the user runs /explain <symbol> or asks to explain how something is implemented.
user-invocable: true
---

You are in explain mode. The user wants to understand a symbol (class, function) or concept (e.g. "compaction", "domain scoring") in the current project.

## Input

The argument is either:
- A **symbol name** — e.g. `LoadTaskHistoryNode`, `_merge_summaries`, `GateCheckNode`
- A **concept** — e.g. "compaction", "task injection", "domain scoring"

## Steps

### 1. Load the code graph

Check if `.code_graph.json` exists in the current repo root:

```bash
ls .code_graph.json 2>/dev/null && echo exists || echo missing
```

If missing, run:
```bash
uv run python scripts/build_code_graph.py
```

Then query it:

```python
import json
g = json.load(open('.code_graph.json'))

# For a symbol name:
modules = g['symbol_index'].get('<symbol>', [])
# → which modules define it

# For each module:
info = g['modules'].get('<module_key>', {})
# → info['file'], info['imports'], info['symbols']

# Who depends on this module:
dependents = g['imported_by'].get('<module_key>', [])
```

### 2. Find the implementation

From `info['file']` and the symbol's `line` number in `info['symbols']`, read the relevant section:

```python
Read(file_path="<repo_root>/<info['file']>", offset=<line-10>, limit=60)
```

### 3. Search task history

Search tasks for the symbol or module name to find historical context — why it was built, how it changed:

```python
mcp__local-mac__tasks__search(query="<symbol or concept>")
```

For each relevant task, optionally fetch its history:
```python
mcp__local-mac__tasks__history(id="<task_id>")
```

### 4. Compose the explanation

Structure your answer as:

```
## <Symbol / Concept>

**Defined in:** `<file>:<line>`
**Kind:** class | function | concept

### What it does
<2–4 sentences — purpose and behaviour>

### Dependencies
- Imports: <what it depends on>
- Used by: <what imports it>

### Implementation
<key code snippet — the core logic, not the whole file>

### History
<1–3 sentences from task history — why it was introduced or how it evolved>
```

## Rules

- If the symbol isn't in the code graph, fall back to `grep` across `*.py` files.
- For concepts (not exact symbol names), use `symbol_index` keys + `imported_by` to find the most relevant modules, then explain the pattern across them.
- Keep the explanation tight — the user is in the middle of work. Lead with what it does, not how you found it.
- If the code graph is stale (meta.commit doesn't match HEAD), note it and offer to regenerate.
