---
name: explain
description: Explain a symbol or concept in the current project — combines code graph lookup, RAG embedding search, file read, and task history for full context. Use when the user runs /explain <symbol> or asks to explain how something is implemented.
user-invocable: true
---

You are in explain mode. The user wants to understand a symbol (class, function) or concept (e.g. "compaction", "domain scoring") in the current project.

## Input

The argument is either:
- A **symbol name** — e.g. `LoadTaskHistoryNode`, `_merge_summaries`, `GateCheckNode`
- A **concept** — e.g. "compaction", "task injection", "domain scoring"

## Steps

### 1. Check staleness

```bash
python3 -c "
import json, subprocess
g = json.load(open('.code_graph.json'))
head = subprocess.check_output(['git','rev-parse','HEAD']).decode().strip()
print('stale' if not head.startswith(g['meta']['commit_short']) else 'fresh')
"
```

If stale, regenerate both:
```bash
uv run python scripts/build_code_graph.py
uv run python scripts/build_code_embeddings.py
```

### 2. Symbol lookup via code graph

```python
import json
g = json.load(open('.code_graph.json'))
modules = g['symbol_index'].get('<symbol>', [])
```

If found → get module info and `imported_by`:
```python
info = g['modules'].get('<module_key>', {})
dependents = g['imported_by'].get('<module_key>', [])
```

**If not found in symbol_index** → the input is a concept, not an exact symbol. Skip to Step 3.

### 3. Concept fallback — RAG embedding search

For concepts or when symbol lookup returns nothing, use the embedding index:

```bash
uv run python scripts/query_code_embeddings.py "<concept or symbol>" --k 5
```

Pick the top 2–3 results with highest scores as the relevant chunks. These already include the source snippet with docstrings.

### 4. Find the implementation

For symbol hits from Step 2 — read the file at the relevant line:
```
Read(file_path="<repo_root>/<info['file']>", offset=<line-5>, limit=60)
```

For concept hits from Step 3 — the snippet is already in the query output. Read more context if needed.

### 5. Search task history

```python
mcp__local-mac__tasks__search(query="<symbol or concept>", status="open,wip,done")
```

Pick 1–2 most relevant tasks for historical context (why it was built, how it changed).

### 6. Compose the explanation

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

- Code graph for exact symbol hits (structural, zero ambiguity). RAG for concepts and fallback.
- Keep the explanation tight — lead with what it does, not how you found it.
- If the code graph is stale, regenerate both graph and embeddings before answering.
- Always check docstrings in the snippet — they are the primary source of "why".
