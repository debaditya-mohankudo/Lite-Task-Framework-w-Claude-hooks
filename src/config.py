"""Central DB path config for claude-hooks.

All database paths live here. Other configs import from this module.

Environment variables (all optional, prefix CLAUDE_HOOKS_):
    CLAUDE_HOOKS_ICLOUD_DB_DIR   override iCloud Databases directory
    CLAUDE_HOOKS_MEMORY_DB       override MEMORY.sqlite path

CWD → domain mapping lives in ~/.claude/cwd_domains.json:
    {"claude-hooks": "claude-hooks", "vault": "vault", ...}
Keys are CWD substrings; first match wins. Auto-created empty on first use.

Valid domains are declared in VALID_DOMAINS below — update when adding a new project.
"""
import json
from pathlib import Path

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

_ICLOUD_DEFAULT = Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Databases"
_CWD_DOMAINS_PATH = Path.home() / ".claude" / "cwd_domains.json"

VALID_DOMAINS: list[str] = [
    "claude-hooks",
    "vault",
    "market-intel",
    "astrology",
    "macos",
    "global",
    "misc",
]


def _load_cwd_domains() -> dict[str, str]:
    """Load CWD→domain map from ~/.claude/cwd_domains.json. Creates empty file on first use."""
    if not _CWD_DOMAINS_PATH.exists():
        _CWD_DOMAINS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CWD_DOMAINS_PATH.write_text("{}\n")
        return {}
    try:
        return json.loads(_CWD_DOMAINS_PATH.read_text())
    except Exception:
        return {}


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
    checkpoints_db: Path = Field(default=Path.home() / ".claude" / "langgraph_checkpoints.db")

    @computed_field
    @property
    def tool_hints_db(self) -> Path:
        return self.icloud_db_dir / "tool_hints.sqlite"

    @computed_field
    @property
    def log_db(self) -> Path:
        return self.icloud_db_dir / "claude_hooks.sqlite"

    @property
    def cwd_domain_map(self) -> dict[str, str]:
        return _load_cwd_domains()

    @property
    def valid_domains(self) -> list[str]:
        return VALID_DOMAINS

    @property
    def memory_valid_types(self) -> list[str]:
        return ["feedback", "user", "project", "reference"]


config = _Config()
