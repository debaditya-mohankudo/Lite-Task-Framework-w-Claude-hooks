#!/usr/bin/env bash
# Hook client — thin curl wrapper for the FastAPI hook server.
# Enriches hook payload with CLAUDE_CWD and POSTs to localhost:8766.
# On server unavailable: fail-open (exit 0, empty JSON response).
#
# Usage: client.sh <HookEvent>
# Events: UserPromptSubmit | PreToolUse | PostToolUse | Stop

set -euo pipefail

EVENT="${1:-}"
SERVER="http://127.0.0.1:8766"

if [ -z "$EVENT" ]; then
  echo "Usage: client.sh <HookEvent>" >&2
  exit 1
fi

payload=$(jq --arg cwd "${CLAUDE_CWD:-}" '. + {cwd: $cwd}')

response=$(echo "$payload" | curl -sf --max-time 2 \
  -H "Content-Type: application/json" \
  "${SERVER}/hook/${EVENT}" \
  -d @-) || {
  echo "claude-hooks: server unreachable for ${EVENT}, failing open" >&2
  echo "{}"
  exit 0
}

echo "$response"
