"""Self-contained config for the langchain_learning package.

Usage:
    from langchain_learning.config import config
    db = config.memory_db

Environment variables (all optional, prefix LC_):
    LC_MEMORY_DB      path to MEMORY.sqlite
    LC_TOOL_HINTS_DB  path to tool_hints.sqlite
    LC_TOP_K          max scored memories to return
    LC_MODEL          Claude model for LLM components
"""
import logging
import sqlite3
from functools import cached_property
from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = logging.getLogger(f"lc.{__name__}")
_ICLOUD = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Databases"


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LC_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        frozen=True,
    )

    memory_db: Path = Field(default=Path.home() / ".claude/MEMORY.sqlite")
    tool_hints_db: Path = Field(default=_ICLOUD / "tool_hints.sqlite")
    top_k: int = Field(default=7)
    model: str = Field(default="claude-haiku-4-5-20251001")

    @computed_field
    @property
    def log_db(self) -> Path:
        return _ICLOUD / "claude_hooks.sqlite"

    @computed_field
    @property
    def stopwords_path(self) -> Path:
        return Path(__file__).parent.parent / "hooks" / "core" / "stopwords.json"

    @cached_property
    def valid_domains(self) -> frozenset[str]:
        try:
            with sqlite3.connect(self.memory_db) as conn:
                rows = conn.execute("SELECT DISTINCT domain FROM memories WHERE domain IS NOT NULL").fetchall()
        except Exception as exc:
            _log.warning("Failed to load valid_domains from MEMORY.sqlite: %s", exc)
            return frozenset({"global"})
        else:
            return frozenset(r[0] for r in rows)


config = Config()
