"""LoadTaskCodeNode — BM25 RAG query over .code_graph.json; replaces git-log commits."""
from __future__ import annotations

from pathlib import Path

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_GRAPH_PATH = Path(__file__).resolve().parents[2] / ".code_graph.json"
_TOP_K = 3


def _build_documents(graph: dict) -> list[dict]:
    """Convert code_graph modules into flat dicts suitable for BM25."""
    docs = []
    for mod_key, mod in graph.get("modules", {}).items():
        symbol_names = " ".join(
            s.get("name", "") for s in mod.get("symbols", [])
        )
        # docstrings not stored in graph — use symbol names + module path as content
        content = f"{mod_key} {symbol_names} {mod.get('file', '')}".strip()
        docs.append({"module": mod_key, "file": mod.get("file", ""), "content": content})
    return docs


def _bm25_query(docs: list[dict], query: str, k: int) -> list[dict]:
    """Run BM25 retrieval using langchain_community.retrievers.BM25Retriever."""
    from langchain_community.retrievers import BM25Retriever
    from langchain_core.documents import Document

    lc_docs = [Document(page_content=d["content"], metadata=d) for d in docs]
    retriever = BM25Retriever.from_documents(lc_docs, k=k)
    results = retriever.invoke(query)
    return [r.metadata for r in results]


class LoadTaskCodeNode:
    """BM25 RAG over .code_graph.json — returns top-3 relevant code modules.

    Replaces LoadTaskCommitsNode. Queries the code graph with the active task
    title using LangChain BM25Retriever (in-memory, no Ollama warmup). Result
    stored as task_rag_chunks and rendered as ## Relevant code in system prompt.

    Tags: task-code, rag, bm25, code-graph, task-context
    """

    def __call__(self, state: SessionState) -> dict:
        entry("load_task_code", state)

        task_title = state.get("active_task_title", "")
        task_id    = state.get("active_task_id", "")

        if not task_id or not task_title:
            return {"task_rag_chunks": []}

        if not _GRAPH_PATH.exists():
            _log.warning("[load_task_code] .code_graph.json not found at %s", _GRAPH_PATH)
            return {"task_rag_chunks": []}

        try:
            import json
            graph = json.loads(_GRAPH_PATH.read_text())
        except Exception as exc:
            _log.error("[load_task_code] failed to load code graph: %s", exc)
            return {"task_rag_chunks": []}

        docs = _build_documents(graph)
        if not docs:
            return {"task_rag_chunks": []}

        try:
            chunks = _bm25_query(docs, task_title, _TOP_K)
        except Exception as exc:
            _log.error("[load_task_code] BM25 query failed: %s", exc)
            return {"task_rag_chunks": []}

        _log.info("[load_task_code] task=%s query=%r chunks=%d modules=%s",
                  task_id[:8], task_title[:40], len(chunks),
                  [c.get("module", "?").split(".")[-1] for c in chunks])
        return {"task_rag_chunks": chunks}
