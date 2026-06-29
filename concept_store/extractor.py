"""One-shot architectural concept extraction from the claude-hooks codebase."""
from __future__ import annotations

import json
from pathlib import Path

import anthropic

from concept_store.store import ConceptStore

_MODEL = "claude-sonnet-4-6"

_SOURCE_FILES = [
    "hooks/dispatcher.py",
    "hooks/gates.py",
    "hooks/server.py",
    "hooks/server_memory.py",
    "hooks/ui/routes.py",
    "hooks/ui/deps.py",
    "src/dispatcher.py",
    "src/tools/tasks.py",
    "src/tools/memory.py",
    "src/tools/hooks.py",
    "src/tools/code_rag.py",
    "src/tools/diff_rag.py",
    "src/tools/rag_core.py",
    "src/db/schema.py",
    "src/config.py",
    "src/logger.py",
    "mcp_server.py",
]

_SYSTEM = (
    "You are an expert software architect performing a one-shot architectural analysis. "
    "Respond with a JSON array only. No prose, no markdown fences."
)

_INSTRUCTIONS = """
Analyze the codebase above and extract architectural concepts.

For each logical unit (file or coherent subsystem), return one JSON object with:
  - name: unique kebab-case slug (e.g. "dispatcher-routes-by-hook-type")
  - module: source file path (e.g. "hooks/dispatcher.py")
  - description: what this module/concept does architecturally (1-3 sentences)
  - invariants: list of strings — constraints that must always hold
  - contracts: list of strings — what this module promises its callers
  - confidence: float 0.0–1.0 — how certain you are about this concept
  - evidence: list of "file:line" strings referencing where you saw this

Return a single top-level JSON array of these objects. Nothing else.
"""


def _read_sources(repo_root: Path) -> str:
    parts = []
    for rel in _SOURCE_FILES:
        path = repo_root / rel
        if not path.exists():
            continue
        content = path.read_text(encoding="utf-8")
        parts.append(f"### {rel}\n{content}")
    return "\n\n".join(parts)


def extract(repo_root: Path, store: ConceptStore, client: anthropic.Anthropic | None = None) -> list[dict]:
    """Read all source files, call Claude once, parse concepts, upsert into store."""
    if client is None:
        client = anthropic.Anthropic()

    source = _read_sources(repo_root)
    user_message = f"{source}\n\n{_INSTRUCTIONS}"

    response = client.messages.create(
        model=_MODEL,
        max_tokens=8192,
        system=_SYSTEM,
        messages=[{"role": "user", "content": user_message}],
    )

    raw = response.content[0].text.strip()
    try:
        concepts = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Claude returned unparseable JSON: {raw[:500]}") from exc

    if not isinstance(concepts, list):
        raise ValueError(f"Expected JSON array, got {type(concepts).__name__}: {raw[:200]}")

    for concept in concepts:
        store.upsert(concept)

    return concepts
