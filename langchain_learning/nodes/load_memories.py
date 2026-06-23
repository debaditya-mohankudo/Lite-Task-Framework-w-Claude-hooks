"""LoadMemoriesNode — retrieves MEMORY.sqlite rows via dense vector search.

Primary path: embed the prompt via Ollama (nomic-embed-text), query
memories_embeddings.tvim (TurboVec, iCloud Databases) for top-5 nearest
memories, fetch full rows from MEMORY.sqlite.

Fallback: if the index is missing or Ollama is unavailable, falls back to
BM25 keyword overlap scoring against all rows.
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.nodes._text_utils import tokenise
from langchain_learning.session_state import SessionState
from src.config import config as _src_cfg
from src.logger import get_logger

_log = get_logger(__name__)

_TOP_N              = 5
_SCORED_BATCH_LIMIT = 200
_ICLOUD_DB          = Path.home() / "Library" / "Mobile Documents" / "com~apple~CloudDocs" / "Databases"
_TVIM_FILE          = _ICLOUD_DB / "memories_embeddings.tvim"
_META_FILE          = _ICLOUD_DB / "memories_embeddings.meta.json"
_MODEL_NAME         = "nomic-embed-text"

# Ensure repo root on path for scripts.build_memories_embeddings
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _recency_multiplier(updated_str: str | None) -> float:
    if not updated_str:
        return 1.0
    try:
        updated = datetime.fromisoformat(updated_str.replace("Z", "+00:00"))
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - updated).days
        if age_days <= 30:
            return 1.2
        if age_days >= 180:
            return 0.8
        return 1.0
    except Exception:
        return 1.0


def _dense_search(prompt: str, project_domain: str | None) -> list[str] | None:
    """Return ordered list of memory names via TurboVec, or None on failure.

    Filters to domain + global allowlist before searching so cross-domain
    memories never compete, giving pure semantic ranking within context.
    """
    if not _TVIM_FILE.exists():
        return None
    try:
        import json
        import turbovec
        from llama_index.embeddings.ollama import OllamaEmbedding

        model = OllamaEmbedding(model_name=_MODEL_NAME, base_url="http://localhost:11434")
        q_vec = np.array([model.get_text_embedding(prompt)], dtype=np.float32)

        index = turbovec.IdMapIndex.load(str(_TVIM_FILE))
        meta  = json.loads(_META_FILE.read_text())

        # Build allowlist: active domain + global (always relevant)
        allowed_domains = {"global"}
        if project_domain:
            allowed_domains.add(project_domain)
        allowlist = np.array(
            [int(uid) for uid, m in meta.items() if m.get("domain") in allowed_domains],
            dtype=np.uint64,
        )

        # search returns (scores, ids) — scores first, ids second
        _scores, ids = index.search(q_vec, _TOP_N, allowlist=allowlist if len(allowlist) else None)
        names = []
        for uid in ids[0]:
            entry_meta = meta.get(str(uid))
            if entry_meta:
                names.append(entry_meta["name"])
        return names
    except Exception as exc:
        _log.warning("[load_memories] dense search failed: %s", exc)
        return None


def _fetch_rows_by_names(names: list[str], conn: sqlite3.Connection) -> list[dict]:
    """Fetch full memory rows in name order."""
    if not names:
        return []
    placeholders = ",".join("?" * len(names))
    rows = conn.execute(
        f"SELECT name, type, domain, tags, body, updated FROM memories WHERE name IN ({placeholders})",
        names,
    ).fetchall()
    row_map = {r["name"]: dict(r) for r in rows}
    return [row_map[n] for n in names if n in row_map]


def _bm25_fallback(tokens: set[str], conn: sqlite3.Connection) -> list[dict]:
    """BM25 keyword overlap fallback when dense index is unavailable."""
    rows_all = conn.execute(
        f"SELECT name, type, domain, tags, body, updated FROM memories LIMIT {_SCORED_BATCH_LIMIT}",
    ).fetchall()
    scored: list[tuple[float, dict]] = []
    for row in rows_all:
        haystack = f"{row['tags'] or ''} {row['body'] or ''}".lower()
        memory_tokens = set(tokenise(haystack))
        overlap = len(tokens & memory_tokens)
        if overlap > 0:
            base = overlap / max(len(tokens), 1)
            score = base * _recency_multiplier(row["updated"])
            scored.append((score, dict(row)))
    scored.sort(key=lambda x: -x[0])
    return [m for _, m in scored][:_TOP_N]


class LoadMemoriesNode:
    """Retrieve top-5 memories for the current prompt via dense vector search.

    Primary: embed prompt → query memories_embeddings.tvim → fetch rows.
    Fallback: BM25 keyword overlap if index missing or Ollama unavailable.

    Tags: memory, memory-injection, dense-search, turbovec, ollama, prompt-context, MEMORY.sqlite
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_memories", state, prompt_len=len(state.get("prompt", "")))

        prompt = state["prompt"]
        tokens = tokenise(prompt.lower())

        if not _cfg.memory_db.exists():
            _log.warning("[load_memories] MEMORY.sqlite not found at %s", _cfg.memory_db)
            return {"memories": [], "keywords": list(tokens)}

        cwd = state.get("cwd", "")
        project_domain = next(
            (domain for key, domain in _src_cfg.cwd_domain_map.items() if key.lower() in cwd.lower()),
            None,
        )

        try:
            conn = sqlite3.connect(f"file:{_cfg.memory_db}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row

            names = _dense_search(prompt, project_domain)
            if names is not None:
                memories = _fetch_rows_by_names(names, conn)
                mode = "dense"
            else:
                memories = _bm25_fallback(tokens, conn)
                mode = "bm25"

            conn.close()
        except Exception as exc:
            _log.error("[load_memories] DB error: %s", exc)
            return {"memories": [], "keywords": list(tokens)}

        names_out = [m.get("name", "?") for m in memories]
        _log.info(
            "[load_memories] mode=%s returned=%d keywords=%d project_domain=%s names=%s",
            mode, len(memories), len(tokens), project_domain, names_out,
        )
        try:
            from hooks.server_memory import record_memories
            record_memories(state.get("session_id", ""), names_out)
        except Exception:
            pass
        return {"memories": memories, "keywords": list(tokens)}
