"""Central DB path config for claude-hooks.

All database paths live here. Other configs import from this module.

Environment variables (all optional, prefix CLAUDE_HOOKS_):
    CLAUDE_HOOKS_ICLOUD_DB_DIR   override iCloud Databases directory
    CLAUDE_HOOKS_MEMORY_DB       override MEMORY.sqlite path

CWD → domain mapping is declared in CWD_DOMAIN_MAP below.
Keys are CWD substrings (matched case-insensitively); first match wins.
Add an entry here when onboarding a new repo.

Valid domains are declared in VALID_DOMAINS below — update when adding a new project.
"""
from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ICLOUD_DEFAULT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Databases"

VALID_DOMAINS: list[str] = [
    "claude-hooks",
    "vault",
    "market-intel",
    "astrology",
    "macos",
    "acme",
    "global",
    "misc",
]

# CWD substring → domain. Keys matched case-insensitively; first match wins.
# Add a new entry here when onboarding a repo instead of editing cwd_domains.json.
CWD_DOMAIN_MAP: dict[str, str] = {
    "claude-hooks": "claude-hooks",
    "vault": "vault",
    "market-intel": "market-intel",
    "astrology": "astrology",
    "K-mirror": "macos",
    "ACME_Cert_Life_Cycle": "acme",
}


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
    tasks_db: Path = Field(default=Path.home() / ".claude" / "proj_tasks.db")

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
    def memory_scoring_json(self) -> Path:
        return self.icloud_db_dir / "memory_scoring.json"

    @property
    def cwd_domain_map(self) -> dict[str, str]:
        return CWD_DOMAIN_MAP

    @property
    def valid_domains(self) -> list[str]:
        return VALID_DOMAINS

    @property
    def memory_valid_types(self) -> list[str]:
        return ["feedback", "user", "project", "reference"]


config = _Config()
