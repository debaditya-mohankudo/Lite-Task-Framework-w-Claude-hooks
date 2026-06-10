"""LoadTaskCodeNode — TurboVec semantic search over .code_embeddings.tvim."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_REPO_ROOT   = Path(__file__).resolve().parents[2]
_TVIM_PATH   = _REPO_ROOT / ".code_embeddings.tvim"
_META_PATH   = _REPO_ROOT / ".code_embeddings.meta.json"
_MAC_SRC     = Path("~/workspace/claude_for_mac_local/src").expanduser()
_TOP_K       = 3
_EMBED_MODEL = "nomic-embed-text"


def _query_tvim(query: str, k: int) -> list[dict]:
    """Embed query with Ollama nomic-embed-text, search TurboVec index, return top-k."""
    if _MAC_SRC not in [Path(p) for p in sys.path]:
        sys.path.insert(0, str(_MAC_SRC))

    from tools.rag_core import load_index, query_index
    from llama_index.embeddings.ollama import OllamaEmbedding

    index, meta = load_index(_TVIM_PATH, _META_PATH)
    if index is None:
        _log.warning("[load_task_code] .code_embeddings.tvim not found or failed to load")
        return []

    embed_model = OllamaEmbedding(model_name=_EMBED_MODEL)
    q_vec = np.array([embed_model.get_text_embedding(query)], dtype=np.float32)

    results = query_index(index, meta, q_vec, k=k)
    return results


class LoadTaskCodeNode:
    """Semantic search over repo's TurboVec index — returns top-3 relevant code symbols.

    Replaces BM25Retriever. Embeds active_task_title via Ollama nomic-embed-text,
    searches .code_embeddings.tvim (built by scripts/build_code_embeddings.py).
    Returns per-symbol hits (module, file, name, kind, line) as task_rag_chunks,
    rendered as ## Relevant code in the system prompt.

    Falls back to empty list if index not found or Ollama unavailable.

    Tags: task-code, rag, turbovec, semantic, embeddings, task-context
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_task_code", state)

        task_title = state.get("active_task_title", "")
        task_id    = state.get("active_task_id", "")

        if not task_id or not task_title:
            return {"task_rag_chunks": []}

        if not _TVIM_PATH.exists():
            _log.warning("[load_task_code] index not found: %s", _TVIM_PATH)
            return {"task_rag_chunks": []}

        try:
            chunks = _query_tvim(task_title, _TOP_K)
        except Exception as exc:
            _log.error("[load_task_code] TurboVec query failed: %s", exc)
            return {"task_rag_chunks": []}

        _log.info("[load_task_code] task=%s query=%r chunks=%d symbols=%s",
                  task_id[:8], task_title[:40], len(chunks),
                  [c.get("name", "?") for c in chunks])
        return {"task_rag_chunks": chunks}
