#!/usr/bin/env bash
# Hook client — thin curl wrapper for the FastAPI hook server.
# Claude Code already sends the session's real cwd on stdin as part of the
# standard hook payload (session_id/transcript_path/cwd/hook_event_name);
# CLAUDE_CWD is only a fallback for the rare case stdin's cwd is missing —
# it must never override a cwd Claude Code already supplied (bug found
# 2026-07-08: the old unconditional `. + {cwd: $cwd}` merge always let the
# right-hand side win, silently clobbering the real per-session cwd with an
# empty/stale env var and making every session's hook payload report
# whatever cwd this long-running server process happened to launch from).
# On server unavailable: fail-open (exit 0, empty JSON response).
#
# Usage: client.sh <HookEvent>
# Events: UserPromptSubmit | PreToolUse | PostToolUse | Stop | SessionEnd

set -euo pipefail

EVENT="${1:-}"
SERVER="${CLAUDE_HOOKS_SERVER:-http://127.0.0.1:8766}"

if [ -z "$EVENT" ]; then
  echo "Usage: client.sh <HookEvent>" >&2
  exit 1
fi

payload=$(jq --arg cwd "${CLAUDE_CWD:-}" '.cwd = (if (.cwd // "") != "" then .cwd else $cwd end)')

response=$(echo "$payload" | curl -sf --max-time 2 \
  -H "Content-Type: application/json" \
  "${SERVER}/hook/${EVENT}" \
  -d @-) || {
  echo "claude-hooks: server unreachable for ${EVENT}, failing open" >&2
  echo "{}"
  exit 0
}

echo "$response"
