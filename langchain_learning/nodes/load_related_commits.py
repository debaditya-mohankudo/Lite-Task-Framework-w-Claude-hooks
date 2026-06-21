"""LoadRelatedCommitsNode — find semantically similar diff hunks via diff_rag TurboVec."""
from __future__ import annotations

import sys
from pathlib import Path

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_CH_ROOT = str(Path(__file__).resolve().parents[2])
_CH_SRC  = str(Path(__file__).resolve().parents[2] / "src")
for _p in (_CH_ROOT, _CH_SRC):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np
from llama_index.embeddings.ollama import OllamaEmbedding
from src.tools.diff_rag import _DEFAULT_REPO, _TVIM_NAME, _META_NAME, _INDEXED_KEY  # noqa: E402
from tools.rag_core import load_index, query_index  # noqa: E402

_log   = get_logger(__name__)
_TOP_N = 3


class LoadRelatedCommitsNode:
    """Semantic diff-hunk search: queries diff_rag for the active task's title+body.

    Skipped when no active task or diff index (.diff_embeddings.tvim) not found.
    Returns top-_TOP_N commit hunks (hash, file, score, snippet[:200]).

    Tags: related-commits, diff-rag, turbovec, task-injection
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_related_commits", state)

        active_id = state.get("active_task_id", "")
        if not active_id:
            _log.info("[load_related_commits] no active task — skipped")
            return {"related_commits": []}

        title = state.get("active_task_title", "")
        body  = state.get("task_body", "") or ""
        query = f"{title}\n{body}".strip()
        if not query:
            return {"related_commits": []}

        try:
            repo_path = _DEFAULT_REPO
            index, meta = load_index(repo_path / _TVIM_NAME, repo_path / _META_NAME)
            if index is None:
                _log.info("[load_related_commits] diff_rag index not found — skipped")
                return {"related_commits": []}

            model = OllamaEmbedding(model_name="nomic-embed-text")
            q_vec = np.array([model.get_text_embedding(query)], dtype=np.float32)
            hits  = query_index(index, meta, q_vec, k=_TOP_N)
            commits = [
                {
                    "commit_hash": h.get("commit_hash", "")[:8],
                    "file":        h.get("file", ""),
                    "score":       round(h.get("score", 0), 3),
                    "snippet":     (h.get("snippet") or "").strip()[:200],
                }
                for h in hits
                if h.get("commit_hash") and str(h.get("commit_hash")) != _INDEXED_KEY
            ]
        except Exception as exc:
            _log.error("[load_related_commits] diff_rag error: %s", exc)
            return {"related_commits": []}

        _log.info(
            "[load_related_commits] task=%s returned=%d hashes=%s",
            active_id, len(commits), [c["commit_hash"] for c in commits],
        )
        return {"related_commits": commits}
