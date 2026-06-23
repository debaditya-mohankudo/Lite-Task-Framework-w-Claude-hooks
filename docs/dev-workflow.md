---
tags: dev workflow, git worktree, deploy.sh, committing, /gc, git commit, testing, deploy, main worktree, dev worktree, claude-hooks-dev, branch, merge, RAG refresh, code embeddings, diff embeddings, one-off deploy, manual path
---
# Development Workflow — Git Worktree

## Overview

The production hook server always runs from `~/workspace/claude-hooks` (main branch).
Development happens in an isolated git worktree so edits never touch the running server
until you deliberately deploy.

```
~/workspace/claude-hooks/          ← production (main branch, server runs here)
~/workspace/claude-hooks/.claude/dev/  ← dev worktree (dev branch, edit here)
```

## Day-to-day loop

```bash
# 1. Work in the dev worktree
cd ~/workspace/claude-hooks/.claude/dev

# 2. Edit files, run tests
uv run python -m pytest tests/ -q

# 3. Commit with /gc (targets dev branch)
/gc

# 4. When ready to ship → deploy
~/workspace/claude-hooks/scripts/deploy.sh
```

`deploy.sh` does: run tests → `git merge dev --no-edit` into main → restart launchd → verify `/health`.

## Key rules

| Rule | Why |
|------|-----|
| Edits go in `.claude/dev/`, not repo root | Repo root is the live server; editing there risks dirty state mid-reload |
| `/gc` uses `--repo ~/workspace/claude-hooks/.claude/dev` | Commits land on dev branch, not main |
| Server auto-reloads via `--reload` after merge | File changes in main are picked up automatically — no manual restart needed for code changes |
| `deploy.sh` is the only path to merge dev→main | Keeps main always passing tests |

## Committing

Use `/gc` from any session. The skill targets the dev worktree automatically.
Include `task:<id>` in every commit; `/gc` injects it when a task is active.

```
feat(area): short description

task:abc123
epic:def456
```

## deploy.sh flow

```
tests pass in .claude/dev/
       ↓
git merge dev --no-edit  (in repo root / main)
       ↓
launchctl unload + load  (restarts hook server)
       ↓
GET /health → {"status":"ok"}
```

If tests fail, deploy aborts before the merge. Fix the failure on dev, then redeploy.

## One-off skipping deploy.sh (manual path)

When pre-existing test failures block deploy.sh, merge and restart manually:

```bash
cd ~/workspace/claude-hooks
git merge dev --no-edit

# Server picks up changes via --reload automatically.
# Only needed if you want a clean restart:
launchctl unload ~/Library/LaunchAgents/com.debaditya.claude-hooks-pipeline.plist
launchctl load  ~/Library/LaunchAgents/com.debaditya.claude-hooks-pipeline.plist

curl -s http://127.0.0.1:8766/health
```

## Setting up the worktree (first time)

```bash
cd ~/workspace/claude-hooks
git worktree add .claude/dev -b dev
```

If the dev branch already exists remotely:

```bash
git worktree add .claude/dev dev
```

Verify:

```bash
git worktree list
# ~/workspace/claude-hooks           abc1234 [main]
# ~/workspace/claude-hooks/.claude/dev  def567 [dev]
```

## RAG index refresh after deploy

After every successful commit on main, refresh the code and diff indexes so
search stays in sync with HEAD:

```python
# code_rag — incremental (changed files only)
mcp__claude-hooks__code_rag__index_files(files=["path/to/changed.py"])

# diff_rag — last commit
mcp__claude-hooks__diff_rag__index_commits(repo=".", since="HEAD~1", max_commits=1)
```

## Checkpoint DB

The server uses `SqliteSaver` at `~/.claude/langgraph_checkpoints.db`.
State persists across reloads and restarts — no context is lost on deploy.
