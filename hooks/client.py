#!/usr/bin/env python3
"""Hook client — thin HTTP wrapper for the FastAPI hook server.

Reads hook JSON payload from stdin, enriches with CLAUDE_CWD, POSTs to
localhost:8766. Fail-open: if server unreachable, exits 0 with empty JSON.

Usage: python3 client.py <HookEvent>
Events: UserPromptSubmit | PreToolUse | PostToolUse | Stop | SessionStart | SessionEnd
"""
import json
import os
import sys
import urllib.error
import urllib.request

EVENT = sys.argv[1] if len(sys.argv) > 1 else ""
if not EVENT:
    print("Usage: client.py <HookEvent>", file=sys.stderr)
    sys.exit(1)

SERVER = os.environ.get("CLAUDE_HOOKS_SERVER", "http://127.0.0.1:8766")

try:
    payload = json.load(sys.stdin)
    payload["cwd"] = os.environ.get("CLAUDE_CWD", "")
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{SERVER}/hook/{EVENT}",
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=2) as resp:
        print(resp.read().decode())
except Exception as exc:
    print(f"claude-hooks: server unreachable for {EVENT}, failing open ({exc})", file=sys.stderr)
    print("{}")
