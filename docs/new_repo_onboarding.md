# Onboarding a New Repo into the Context System

When a new repo `<new_repo>` starts development, two things need to happen:
1. The domain classifier learns to recognise it by CWD and prompt keywords
2. MEMORY.sqlite gets seeded with project-level memories for that domain

---

## Step 1 — Add to `cwd_domain_map`

File: `~/Library/Mobile Documents/com~apple~CloudDocs/Databases/domain_classifier.json`

```json
"cwd_domain_map": {
  ...
  "<new_repo>": "<new_repo>"
}
```

This makes `CwdDomainDetectNode` fire `domain=<new_repo>` deterministically whenever Claude is working inside that directory. Without this, domain detection falls back to keyword scoring — unreliable.

---

## Step 2 — Add `keyword_signals`

Under `keyword_signals`, add a block for the new domain:

```json
"<new_repo>": {
  "strong": {
    "<core_term_1>": 5,
    "<core_term_2>": 4
  },
  "weak": {
    "<related_term>": 2
  }
}
```

**Strong signals (4–5):** terms that almost exclusively appear in this project's context.  
**Weak signals (1–2):** terms that are relevant but shared with other domains.

---

## Step 3 — Add `combination_signals`

Under `combination_signals`, add two-word pairs that together clearly signal this project:

```json
"<new_repo>": [
  [["<term_a>", "<term_b>"], 5],
  [["<term_c>", "<term_d>"], 4]
]
```

Good combinations are pairs that individually are weak but together are unambiguous (e.g. `["hook", "pipeline"]` for claude-hooks).

---

## Step 4 — Seed MEMORY.sqlite

Use the MCP memory tools to create at least these two memories:

### 4a. Goals memory (priority 1 — always injected)

```python
mcp__local-mac__memory__add(
  name="<new_repo>-goals",
  type="project",
  domain="<new_repo>",
  priority=1,
  tags="<new_repo>,goals,mission,direction",
  body="""Most important goal: <one-line mission statement>

<project description, current direction, key constraints>

What recency pull looks like for this project — recognize and resist:
- <distraction pattern 1>
- <distraction pattern 2>

The test: at the end of a session, did the work move the mission forward?"""
)
```

### 4b. Architecture memory (priority 10)

```python
mcp__local-mac__memory__add(
  name="<new_repo>-arch",
  type="project",
  domain="<new_repo>",
  priority=10,
  tags="<new_repo>,architecture,files,stack",
  body="""Stack: <language, frameworks>

Key files:
- <file1> — <purpose>
- <file2> — <purpose>

Databases / external deps:
- <db or service> — <purpose>"""
)
```

Add further memories (feedback, reference) as the project evolves.

---

## Step 5 — Verify

Start a new Claude Code session in the repo and check `## Injected memories` in the system prompt. You should see:
- `<new_repo>-goals` (always, priority 1)
- `<new_repo>-arch` and any domain-matched memories

If they're missing, check:
1. `cwd_domain_map` key matches the actual directory name exactly (case-sensitive)
2. Memories have `domain=<new_repo>` (not `global` or another domain)
3. The `_cache` in `load_classifier_config.py` is cleared (restart the hook process)

---

## Reference: claude-hooks as a worked example

| Step | Value |
|------|-------|
| cwd_domain_map key | `claude-hooks` |
| domain | `claude-hooks` |
| Goals memory | `claude-hooks-goals` (priority 1) |
| Arch memory | `claude-hooks-arch` (priority 10) |
| keyword_signals | `memory_loader`, `session_graph`, `pre_tool_use`, `langgraph`, … |
| combination_signals | `["memory", "inject"]`, `["hook", "pipeline"]`, `["goals", "memory"]`, … |
