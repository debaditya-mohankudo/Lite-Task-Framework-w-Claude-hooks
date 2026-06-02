"""Self-contained config for the langchain_learning package.

Singleton Config — instantiated once at import time, read-only properties.
No module-level constants that callers can accidentally overwrite.

Usage:
    from langchain_learning.config import config
    db = config().memory_db

Environment variables (all optional):
    LC_MEMORY_DB      path to MEMORY.sqlite
    LC_TOOL_HINTS_DB  path to tool_hints.sqlite
    LC_TOP_K          max scored memories to return
    LC_MODEL          Claude model for LLM components (Component 3)
    LC_SERVER_HOST    host for LangServe (Option C)
    LC_SERVER_PORT    port for LangServe (Option C)
"""
import os
from pathlib import Path


class Config:
    _instance: "Config | None" = None

    def __new__(cls) -> "Config":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @property
    def memory_db(self) -> Path:
        return Path(os.getenv("LC_MEMORY_DB", str(Path.home() / ".claude/MEMORY.sqlite")))

    @property
    def tool_hints_db(self) -> Path:
        return Path(
            os.getenv(
                "LC_TOOL_HINTS_DB",
                str(Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Databases/tool_hints.sqlite"),
            )
        )

    @property
    def log_db(self) -> Path:
        return Path.home() / "Library/Mobile Documents/com~apple~CloudDocs/Databases/claude_hooks.sqlite"

    @property
    def stopwords_path(self) -> Path:
        return Path(__file__).parent.parent / "hooks" / "core" / "stopwords.json"

    @property
    def top_k(self) -> int:
        return int(os.getenv("LC_TOP_K", "7"))

    @property
    def model(self) -> str:
        return os.getenv("LC_MODEL", "claude-haiku-4-5-20251001")

    @property
    def server_host(self) -> str:
        return os.getenv("LC_SERVER_HOST", "127.0.0.1")

    @property
    def server_port(self) -> int:
        return int(os.getenv("LC_SERVER_PORT", "8766"))

    @property
    def valid_domains(self) -> frozenset[str]:
        return frozenset({
            "astrology",
            "philosophy",
            "market-intel",
            "vault",
            "macos",
            "coding-best-practices",
            "health",
            "acme",
            "session",
            "global",
        })
