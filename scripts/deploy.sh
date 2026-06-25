#!/usr/bin/env bash
# Two-phase deploy:
#   deploy.sh          → dev → test  (run tests, server reloads from test worktree)
#   deploy.sh --ship   → test → main (final merge to main, no tests)
#
# Server always runs from ~/workspace/claude-hooks-test (test branch, --reload on).
# Never touch main directly — only --ship merges into it.

set -euo pipefail

MAIN_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEV_DIR="$(dirname "$MAIN_DIR")/claude-hooks-dev"
TEST_DIR="$(dirname "$MAIN_DIR")/claude-hooks-test"

SHIP=false
if [[ "${1:-}" == "--ship" ]]; then
    SHIP=true
fi

echo "=== claude-hooks deploy ==="

if $SHIP; then
    # --- Phase 2: test → main (ship) ---
    echo "Merging test → main..."
    cd "$MAIN_DIR"
    git merge test --no-edit
    echo "=== Shipped to main. ==="
    exit 0
fi

# --- Phase 1: dev → test ---

# 1. Confirm worktrees exist
for DIR in "$DEV_DIR" "$TEST_DIR"; do
    if [ ! -d "$DIR/.git" ] && [ ! -f "$DIR/.git" ]; then
        echo "ERROR: worktree not found at $DIR" >&2
        exit 1
    fi
done

# 2. Quick unit gate in dev (no server needed)
echo "Running unit tests in dev worktree..."
cd "$DEV_DIR"
uv run python -m pytest tests/ -q -m "not integration"
echo "Unit tests passed."

# 3. Merge dev → test (server auto-reloads via --reload)
echo "Merging dev → test..."
cd "$TEST_DIR"
git merge dev --no-edit

# Force uvicorn --reload to detect the change — git merge on macOS doesn't
# always update mtimes in a way that watchfiles notices, so the server can
# keep running the pre-merge code indefinitely without this touch.
touch "$TEST_DIR/hooks/server.py"

# 4. Verify health
sleep 2
HEALTH=$(curl -sf --max-time 5 http://127.0.0.1:8766/health || echo '{"status":"unreachable"}')
echo "Health (post-reload): $HEALTH"

STATUS=$(echo "$HEALTH" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status','?'))" 2>/dev/null || echo "?")
if [ "$STATUS" != "ok" ]; then
    echo "WARNING: Server health check returned: $HEALTH" >&2
    exit 1
fi

# 5. Full suite (unit + integration) from test worktree against live server
echo "Running full test suite from test worktree..."
uv run python -m pytest tests/ -q
echo "=== Deploy complete. Server is up on test. Run 'deploy.sh --ship' to merge to main. ==="
