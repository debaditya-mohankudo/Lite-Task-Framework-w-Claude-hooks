#!/usr/bin/env bash
# Deploy dev→main: merge the dev worktree branch into main and restart the hook server.
#
# Usage: scripts/deploy.sh
#
# Requires: the dev worktree exists at ~/workspace/claude-hooks-dev (git worktree add)
# The production server always runs from ~/workspace/claude-hooks (main).

set -euo pipefail

PROD_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEV_DIR="$(dirname "$PROD_DIR")/claude-hooks-dev"
PLIST_LABEL="com.debaditya.claude-hooks-pipeline"
PLIST_DST="$HOME/Library/LaunchAgents/${PLIST_LABEL}.plist"

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

# 4. Restart the launchd server
echo "Restarting hook server..."
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

# 5. Wait briefly and verify health
sleep 2
HEALTH=$(curl -sf --max-time 5 http://127.0.0.1:8766/health || echo '{"status":"unreachable"}')
echo "Health: $HEALTH"

STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || echo "?")
if [ "$STATUS" = "ok" ]; then
    echo "=== Deploy complete. Server is up. ==="
else
    echo "WARNING: Server health check returned: $HEALTH" >&2
    exit 1
fi
