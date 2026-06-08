"""Central DB path config for claude-hooks.

All database paths live here. Other configs import from this module.

Environment variables (all optional, prefix CLAUDE_HOOKS_):
    CLAUDE_HOOKS_ICLOUD_DB_DIR   override iCloud Databases directory
    CLAUDE_HOOKS_MEMORY_DB       override MEMORY.sqlite path
    CLAUDE_HOOKS_SESSIONS_DB     override sessions.db path
"""
from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ICLOUD_DEFAULT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Databases"


class _Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CLAUDE_HOOKS_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    icloud_db_dir: Path = Field(default=_ICLOUD_DEFAULT)
    memory_db: Path = Field(default=Path.home() / ".claude" / "MEMORY.sqlite")
    sessions_db: Path = Field(default=Path.home() / ".claude" / "sessions.db")
    tasks_db: Path = Field(default=Path.home() / ".claude" / "proj_tasks.db")
    checkpoints_db: Path = Field(default=Path.home() / ".claude" / "langgraph_checkpoints.db")

    @computed_field
    @property
    def tool_hints_db(self) -> Path:
        return self.icloud_db_dir / "tool_hints.sqlite"

    @computed_field
    @property
    def log_db(self) -> Path:
        return self.icloud_db_dir / "claude_hooks.sqlite"

    @computed_field
    @property
    def domain_classifier_json(self) -> Path:
        return self.icloud_db_dir / "domain_classifier.json"

    @computed_field
    @property
    def prompt_id_tmp(self) -> Path:
        return Path.home() / ".claude" / "current_prompt_id.tmp"

    @property
    def memory_valid_types(self) -> list[str]:
        return ["feedback", "user", "project", "reference"]


config = _Config()
