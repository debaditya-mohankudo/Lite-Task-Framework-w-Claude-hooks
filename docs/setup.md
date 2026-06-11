# Project Setup Guide

How to get claude-hooks running from scratch on a new machine or when sharing the project with someone.

---

## Prerequisites

- macOS with [Claude Code](https://claude.ai/code) installed
- [uv](https://docs.astral.sh/uv/) installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- iCloud Drive enabled (two databases live there — see below)
- An Anthropic API key

---

## 1. Clone and install dependencies

```bash
git clone <repo_url> ~/workspace/claude-hooks
cd ~/workspace/claude-hooks
uv sync
```

---

## 2. Create the iCloud Databases directory

Two databases and the domain classifier config live in iCloud so they sync across machines:

```bash
mkdir -p ~/Library/Mobile\ Documents/com~apple~CloudDocs/Databases
```

If iCloud is not available, set the override env var (see [Environment variables](#5-environment-variables)):

```bash
export CLAUDE_HOOKS_ICLOUD_DB_DIR=~/.claude/databases
mkdir -p ~/.claude/databases
```

---

## 3. Create the databases

All databases are auto-created on first use **except** `domain_classifier.json`, which must exist before the first hook run.

### Auto-created (nothing to do)

| Database | Location | Created by |
|----------|----------|------------|
| `MEMORY.sqlite` | `~/.claude/MEMORY.sqlite` | First `memory__add` MCP call |
| `proj_tasks.db` | `~/.claude/proj_tasks.db` | First `tasks__create` MCP call |
| `langgraph_checkpoints.db` | `~/.claude/langgraph_checkpoints.db` | First hook run |
| `tool_hints.sqlite` | iCloud `Databases/tool_hints.sqlite` | First `post_tool_use` hook run |
| `claude_hooks.sqlite` | iCloud `Databases/claude_hooks.sqlite` | First hook run |

### Must be created manually

**`domain_classifier.json`** — tells the classifier which keywords map to which domain.

Copy the template from the repo:

```bash
cp ~/workspace/claude-hooks/docs/domain_classifier_template.json \
   ~/Library/Mobile\ Documents/com~apple~CloudDocs/Databases/domain_classifier.json
```

> If no template exists yet, create a minimal version:
> ```json
> {
>   "cwd_domain_map": {},
>   "default_domain": "global",
>   "classify_threshold": 2,
>   "keyword_signals": {},
>   "combination_signals": {},
>   "negative_signals": {}
> }
> ```

To register your repos in the classifier, follow [new_repo_onboarding.md](new_repo_onboarding.md).

---

## 4. Register the hooks in `~/.claude/settings.json`

Add the following to your global Claude Code settings (`~/.claude/settings.json`). Replace the path if you cloned to a different location.

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "uv run --project /Users/<you>/workspace/claude-hooks python3 ~/.claude/hooks/dispatcher.py UserPromptSubmit"
          }
        ]
      }
    ],
    "PreToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "uv run --project /Users/<you>/workspace/claude-hooks python3 ~/.claude/hooks/dispatcher.py PreToolUse"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "uv run --project /Users/<you>/workspace/claude-hooks python3 ~/.claude/hooks/dispatcher.py PostToolUse"
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "uv run --project /Users/<you>/workspace/claude-hooks python3 ~/.claude/hooks/dispatcher.py Stop"
          }
        ]
      }
    ]
  }
}
```

---

## 5. Environment variables

All variables are optional. Set them in `~/.claude/.env` or export in your shell.

| Variable | Default | Purpose |
|----------|---------|---------|
| `ANTHROPIC_API_KEY` | — | Required for LLM-based domain classification (`LC_MODEL`) |
| `CLAUDE_HOOKS_ICLOUD_DB_DIR` | `~/Library/Mobile Documents/.../Databases` | Override iCloud path (e.g. when iCloud unavailable) |
| `CLAUDE_HOOKS_MEMORY_DB` | `~/.claude/MEMORY.sqlite` | Override memory DB path |
| `LC_DEV_MODE` | `false` | Set `true` to surface hook errors inline in Claude Code (exit 2 on exception). Use during development only. |
| `LC_TOP_K` | `7` | Max number of scored memories returned per prompt |
| `LC_MODEL` | `claude-haiku-4-5-20251001` | Claude model used for LLM classification nodes |

---

## 6. Verify the setup

Start a new Claude Code session and check the system prompt for `## Injected memories`. If the block appears, the `UserPromptSubmit` hook is running correctly.

To check hook logs:

```
mcp__local-mac__hooks__read_logs_sqlite
```

To test manually:

```bash
cd ~/workspace/claude-hooks
echo '{"session_id":"test","prompt":"test prompt","cwd":"/tmp"}' | \
  uv run python3 ~/.claude/hooks/dispatcher.py UserPromptSubmit
```

A successful run exits 0 and emits JSON with `additionalSystemPrompt`.

---

## 7. Seed initial memories (optional but recommended)

The system works without any memories, but seeding a few project-level facts immediately improves context quality.

Follow the memory seeding steps in [new_repo_onboarding.md](new_repo_onboarding.md) for each repo you work in.

---

## Troubleshooting

**Hooks not firing** — check that `~/.claude/settings.json` has the correct absolute path to the repo and that `uv` is on PATH (`which uv`). Use the full path `/Users/<you>/.local/bin/uv` if needed (see `mcp-server-uv-full-path` memory).

**`domain_classifier.json` not found** — the hook will log a warning and fall back to empty config. Create the file as shown in step 3.

**iCloud path errors** — set `CLAUDE_HOOKS_ICLOUD_DB_DIR` to a local directory and restart.

**Silent failures** — set `LC_DEV_MODE=true` in `~/.claude/.env` to make hook errors surface inline in Claude Code.
