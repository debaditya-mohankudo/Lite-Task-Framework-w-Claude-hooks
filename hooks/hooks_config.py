#!/usr/bin/env python3
"""Shared configuration for all hooks — pydantic-settings singleton."""
from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class HooksConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLAUDE_HOOKS_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    icloud_db_dir: Path = Field(
        default=Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Databases",
        description="iCloud Databases directory",
    )
    sessions_db: Path = Field(
        default=Path.home() / ".claude" / "sessions.db",
        description="Session state DB",
    )
    @computed_field
    @property
    def log_db_path(self) -> Path:
        return self.icloud_db_dir / "claude_hooks.sqlite"

    @computed_field
    @property
    def tool_hints_db(self) -> Path:
        return self.icloud_db_dir / "tool_hints.sqlite"


cfg = HooksConfig()
