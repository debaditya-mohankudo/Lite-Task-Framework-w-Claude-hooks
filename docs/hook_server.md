---
tags: hook server, FastAPI, uvicorn, server management, health check, port 8766, server restart, server reload, persistent server, hook server startup, deploy.sh, server lifecycle, why persistent server
---
# Hook Server

`hooks/server.py` — persistent FastAPI process replacing per-invocation subprocess dispatcher.

## Architecture

```
Claude Code hook fires
        │
        ▼
~/.claude/settings.json
  → python3 hooks/client.py <Event>
        │
        ▼ curl POST localhost:8766
  FastAPI server (persistent, launchd-managed)
        │
        ▼
  hooks/dispatcher.py handler
        │
        ▼
  LangGraph session graph (SqliteSaver, in-process)
```

## Why a persistent server

The old model ran `dispatcher.py` as a fresh subprocess on every hook event. Three problems:

1. **Daemon thread death** — `threading.Thread(daemon=True)` threads are killed when the subprocess exits. PostToolUse fire-and-forget threads died before writing tool usage to the checkpoint, breaking gate prereq tracking (e.g. `mail__delete` kept blocking even after `mail__read` ran).
2. **SqliteSaver I/O** — LangGraph checkpointed state to SQLite after every node, adding ~600ms per hook invocation.
3. **Cold start** — every subprocess re-imported LangGraph, loaded config, opened DB connections.

A persistent server solves all three: threads never die, SqliteSaver runs in-process (no subprocess cold start), imports happen once at startup.

## Performance

### Before (SqliteSaver, subprocess model)

Each LangGraph node = 1 checkpoint read + 1 checkpoint write to SQLite.

| Hook | Nodes fired | DB calls eliminated |
|------|-------------|---------------------|
| UserPromptSubmit (no active task) | 7 | 14 |
| UserPromptSubmit (active task) | 10 | 20 |
| PreToolUse | 1 | 2 |
| PostToolUse | 1–2 | 2–4 |

A typical busy session turn (UPS + PreTU×N + PTU×N + Stop) eliminated **50–100 checkpoint DB calls per turn**.

### After (SqliteSaver, persistent server)

Checkpoint reads/writes are in-process SqliteSaver calls — no subprocess spawning, no cold start. SQLite I/O is present but cheap compared to the eliminated subprocess overhead.

| Route | Observed latency |
|-------|-----------------|
| `GET /health` | ~2ms |
| `POST /hook/PreToolUse` (cached) | 6ms |
| `POST /hook/PostToolUse` | 15–25ms |
| `POST /hook/Stop` | 23–25ms |
| `POST /hook/UserPromptSubmit` | 45–48ms |

Pipeline overhead dropped from ~600ms → ~20ms per hook call (~30× improvement).

## Session lifecycle

SqliteSaver checkpoints all session state to `~/.claude/langgraph_checkpoints.db` keyed by `session_id`. State survives server restarts. On `SessionEnd` (a dedicated Claude Code lifecycle event, NOT Stop), the server evicts the checkpoint for that session. Stop fires every turn and must never evict checkpoints — doing so wipes cross-turn state.

```bash
# SessionEnd is registered in ~/.claude/settings.json alongside the other hooks
POST /hook/SessionEnd  → evicts the checkpoint for session_id
```

## Client

`hooks/client.py` — thin HTTP wrapper (stdlib urllib, no curl/jq needed):
- Reads stdin (Claude hook JSON payload)
- Enriches with `CLAUDE_CWD` env var
- POSTs to `http://127.0.0.1:8766/hook/<Event>`
- Fail-open on server unavailable (exits 0, returns `{}`)

## Observability

Every HTTP request is logged to `claude_hooks.sqlite` under logger `server`:

```
server | INFO | HTTP POST /hook/PostToolUse → 200  22ms
```

Query via MCP: `mcp__claude-hooks__hooks__read_logs_sqlite` with `logger: "server"`.

## Managing the server

Managed by launchd (`KeepAlive=true`, `RunAtLoad=true`).

```bash
# Install / restart
bash scripts/install_server.sh

# Health check
curl http://127.0.0.1:8766/health
# → {"status": "ok", "sessions": 0}

# Logs
tail -f /tmp/claude-hooks-pipeline.log
tail -f /tmp/claude-hooks-pipeline.err
```

Plist: `launchd/com.debaditya.claude-hooks-pipeline.plist`
