"""Minimal config for claude-hooks MCP server."""
from pathlib import Path


class _Config:
    @property
    def memory_db(self) -> Path:
        return Path.home() / ".claude" / "MEMORY.sqlite"

    @property
    def tool_hints_db(self) -> Path:
        return Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Databases/tool_hints.sqlite"

    @property
    def memory_valid_types(self) -> list[str]:
        return ["feedback", "user", "project", "reference"]


config = _Config()
