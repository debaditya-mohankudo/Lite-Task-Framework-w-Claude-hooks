#!/usr/bin/env bash
# Deploy dev→main: merge the dev worktree branch into main. The server runs with --reload
# and picks up file changes automatically — no process restart needed.
#
# Usage: scripts/deploy.sh
#
# Requires: the dev worktree exists at ~/workspace/claude-hooks-dev (git worktree add)
# The production server always runs from ~/workspace/claude-hooks (main).

set -euo pipefail

PROD_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEV_DIR="$(dirname "$PROD_DIR")/claude-hooks-dev"
echo "=== claude-hooks deploy ==="

# 1. Confirm dev worktree exists
if [ ! -d "$DEV_DIR/.git" ] && [ ! -f "$DEV_DIR/.git" ]; then
    echo "ERROR: dev worktree not found at $DEV_DIR" >&2
    echo "Run: git worktree add ../claude-hooks-dev -b dev" >&2
    exit 1
fi

# 2. Run tests in the dev worktree before merging
echo "Running tests in dev worktree..."
cd "$DEV_DIR"
uv run python -m pytest tests/ -q
echo "Tests passed."

# 3. Merge dev → main
echo "Merging dev → main..."
cd "$PROD_DIR"
git merge dev --no-edit

# 4. Wait briefly for --reload to pick up changes, then verify health
sleep 2
HEALTH=$(curl -sf --max-time 5 http://127.0.0.1:8766/health || echo '{"status":"unreachable"}')
echo "Health (post-reload): $HEALTH"

STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || echo "?")
if [ "$STATUS" = "ok" ]; then
    echo "=== Deploy complete. Server is up. ==="
else
    echo "WARNING: Server health check returned: $HEALTH" >&2
    exit 1
fi
