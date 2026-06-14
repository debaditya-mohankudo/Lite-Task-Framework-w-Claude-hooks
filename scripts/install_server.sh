#!/usr/bin/env bash
# Install and (re)load the claude-hooks FastAPI server as a launchd agent.
# Safe to run multiple times — unloads first to avoid stale registration.

set -euo pipefail

PLIST_SRC="$(cd "$(dirname "$0")/.." && pwd)/launchd/com.debaditya.claude-hooks-pipeline.plist"
PLIST_DST="$HOME/Library/LaunchAgents/com.debaditya.claude-hooks-pipeline.plist"

echo "Unloading existing agent (if any)..."
launchctl unload "$PLIST_DST" 2>/dev/null || true

echo "Installing plist..."
cp "$PLIST_SRC" "$PLIST_DST"

echo "Loading agent..."
launchctl load "$PLIST_DST"

echo "Done. Server should be running on port 8766."
echo "Logs: /tmp/claude-hooks-pipeline.log"
echo "Health: curl http://127.0.0.1:8766/health"
