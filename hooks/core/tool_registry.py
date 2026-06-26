"""Single source of truth for tool→domain and tool→skill mappings.

Personal mappings live in iCloud: ~/Library/Mobile Documents/com~apple~CloudDocs/Databases/tool_registry.json
New users: create that file with your own tool_domain_map and tool_skill_map dicts.
Falls back to empty dicts if the file is missing.
"""
import json
import re

from hooks.paths import TOOL_REGISTRY_PATH as _REGISTRY_PATH

_registry: dict = {}
if _REGISTRY_PATH.exists():
    try:
        _registry = json.loads(_REGISTRY_PATH.read_text())
    except Exception:
        pass

TOOL_DOMAIN_MAP: dict[str, str] = _registry.get("tool_domain_map", {})
TOOL_SKILL_MAP: dict[str, str] = _registry.get("tool_skill_map", {})

_MCP_PREFIX = re.compile(r"^mcp__[^_]+__")


def strip_mcp_prefix(tool_name: str) -> str:
    """'mcp__local-mac__vault__read' → 'vault__read'"""
    return _MCP_PREFIX.sub("", tool_name)


def infer_domain(short_name: str) -> str:
    for prefix, domain in TOOL_DOMAIN_MAP.items():
        if short_name.startswith(prefix):
            return domain
    return "global"


def infer_skill(short_name: str) -> str:
    return TOOL_SKILL_MAP.get(short_name, "")
