---
tags: new repo onboarding, CWD_DOMAIN_MAP, domain setup, MEMORY.sqlite seeding, code embeddings, diff embeddings, task embeddings, config.py, onboarding steps, /onboarding skill, new project setup, domain registration
---
# Onboarding a New Repo into the Context System

When a new repo starts development, two things need to happen:

1. Domain detection learns to recognise it from CWD
2. `MEMORY.sqlite` gets seeded with project-level memories for that domain

---

## Step 1 — Add to `CWD_DOMAIN_MAP` in `src/config.py`

Open `src/config.py` and add an entry to `CWD_DOMAIN_MAP`:

```python
CWD_DOMAIN_MAP: dict[str, str] = {
    "claude-hooks": "claude-hooks",
    "vault":        "vault",
    "market-intel": "market-intel",
    "<repo-dirname>": "<domain-name>",  # ← add this
}
```

Keys are CWD substrings (matched case-insensitively); first match wins. If the domain is new, also add it to `VALID_DOMAINS` in the same file.

The change takes effect on the next hook run — no restart needed.

---

## Step 2 — Seed `MEMORY.sqlite`

Use the MCP memory tools to create at least these two memories:

### Goals memory (priority 1 — always injected)

```python
mcp__local-mac__memory__add(
    name="<repo>-goals",
    type="project",
    domain="<domain>",
    priority=1,
    tags="<domain>,goals,mission,direction",
    body="""Most important goal: <one-line mission statement>

<project description, current direction, key constraints>

What recency pull looks like for this project — recognise and resist:
- <distraction pattern 1>
- <distraction pattern 2>

The test: at the end of a session, did the work move the mission forward?"""
)
```

### Architecture memory (priority 10)

```python
mcp__local-mac__memory__add(
    name="<repo>-arch",
    type="project",
    domain="<domain>",
    priority=10,
    tags="<domain>,architecture,files,stack",
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

## Step 3 — Verify

Start a new Claude Code session in the repo and check `## Injected memories` in the system prompt. You should see:

- `<repo>-goals` (always, priority 1)
- `<repo>-arch` and any domain-matched memories

If they're missing, check:

1. `CWD_DOMAIN_MAP` key in `src/config.py` matches the actual directory name (substring match is case-insensitive)
2. Memories have `domain=<domain>` (not `global` or another domain)
3. Domain value is in `VALID_DOMAINS` in `src/config.py`

---

## Reference: claude-hooks as a worked example

| Step | Value |
| --- | --- |
| `CWD_DOMAIN_MAP` key | `claude-hooks` |
| domain | `claude-hooks` |
| Goals memory | `claude-hooks-goals` (priority 1) |
| Arch memory | `claude-hooks-arch` (priority 10) |

---

← [Architecture](ARCHITECTURE.md) · [Setup Guide](setup.md) · [Databases](arch/databases.md)
