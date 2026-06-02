#!/usr/bin/env python3
"""Shared configuration for all hooks."""
from pathlib import Path

ICLOUD_DB_DIR = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Databases"

# Central log DB — all hook errors and events
LOG_DB_PATH = ICLOUD_DB_DIR / "claude_hooks.sqlite"

# Session state DB — fixed path so hooks work regardless of which repo they live in
SESSIONS_DB = Path.home() / ".claude" / "sessions.db"

# FastAPI memory server
HOOK_SERVER_BASE = "http://127.0.0.1:8765"
