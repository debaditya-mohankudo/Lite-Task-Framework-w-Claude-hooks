---
name: deploy
description: Deploy claude-hooks dev→test→main. Runs unit gate in dev, merges to test, runs full suite (unit + integration) from test, then ships to main. Use when ready to ship a feature branch to production.
user-invocable: true
updated: 2026-06-24
---

Deploy `claude-hooks` through the full pipeline: dev → test → main.

## Steps

### 1. Deploy dev → test and run full suite

```bash
~/workspace/claude-hooks/scripts/deploy.sh
```

This script:
- Runs unit tests in dev worktree (`-m "not integration"`) as a quick gate
- Merges dev → test, then restarts the server via `launchctl kickstart -k`
- Waits for health check at `http://127.0.0.1:8766/health`
- Runs the full test suite (unit + integration) from the test worktree against the live server

If any step fails, stop and report the failure. Do not proceed to step 2.

### 2. Ship test → main

```bash
~/workspace/claude-hooks/scripts/deploy.sh --ship
```

This merges test → main. No tests run here — they already passed in step 1.

### 3. Done

Report:
```
✓ Deployed to main.
  Unit gate:   passed (dev)
  Full suite:  passed (test)
  main is now at: <git log --oneline -1 ~/workspace/claude-hooks>
```

## Rules

- Never skip the unit gate or full suite — don't pass `--no-verify` or comment out test steps.
- If the health check fails after merge, stop — the server didn't restart cleanly. Check `launchctl list | grep claude-hooks` and `/tmp/claude-hooks-server.log` for errors.
- If integration tests fail, stop and report which tests failed. Do not ship to main.
- This skill only applies to the `claude-hooks` project (worktrees at `~/workspace/claude-hooks-dev`, `~/workspace/claude-hooks-test`, `~/workspace/claude-hooks`).
