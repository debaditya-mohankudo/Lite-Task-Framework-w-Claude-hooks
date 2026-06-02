"""Component 5 — LCEL Pipeline.

LangChain concept: LCEL (LangChain Expression Language)
Replaces: hooks/memory_loader.py keyword/scoring pipeline (conceptually)

LCEL uses the pipe operator (|) to compose Runnables into a chain:

    runnable_a | runnable_b | runnable_c

Each step receives the output of the previous one. Every LangChain primitive
(LLM, retriever, prompt, output parser) is a Runnable. Custom logic wraps in
RunnableLambda. Parallel branches run with RunnableParallel.

Pipeline shape:

    prompt_dict
        │
        ▼
    [classify_domain]          ← RunnableLambda wrapping DomainClassifier
        │
        ▼ {prompt, domains, ...}
    [RunnableParallel]         ← branches run concurrently
      ├── memories             ← SQLiteMemoryRetriever.invoke(prompt)
      └── tool_hints           ← ToolHintsRetriever scoped to detected domains
        │
        ▼ merged dict
    [format_output]            ← RunnableLambda — assembles final MemoryContext
        │
        ▼
    MemoryContext (typed dict)

LangChain concepts demonstrated:
  - RunnableLambda         wrap plain functions as pipeline steps
  - RunnableParallel       fan-out: run two retrievers concurrently
  - pipe operator (|)      compose steps left-to-right
  - .invoke() / .batch()   standard Runnable invocation
  - LCEL passthrough       RunnablePassthrough carries inputs forward
"""
from __future__ import annotations

from langchain_learning.logger import get_logger
from typing import Any, TypedDict

from langchain_core.documents import Document
from langchain_core.runnables import RunnableLambda, RunnableParallel, RunnablePassthrough

from langchain_learning.config import Config
_cfg = Config()
from langchain_learning.domain_classifier import DomainClassifier, make_classifier_runnable
from langchain_learning.memory_retriever import SQLiteMemoryRetriever
from langchain_learning.tool_hints_retriever import ToolHintsRetriever

_log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Output type
# ---------------------------------------------------------------------------

class MemoryContext(TypedDict):
    """Structured result returned by the pipeline for one prompt turn."""
    prompt: str
    domains: list[str]
    memories: list[dict]       # each: {name, domain, priority, body, score}
    tool_hints: list[dict]     # each: {tool_name, domain, skill, count}


# ---------------------------------------------------------------------------
# Step helpers (each becomes a RunnableLambda node)
# ---------------------------------------------------------------------------

def _docs_to_dicts(docs: list[Document]) -> list[dict]:
    """Flatten Document list → list of plain dicts (metadata + page_content)."""
    result = []
    for doc in docs:
        entry = dict(doc.metadata)
        entry.setdefault("body", doc.page_content)
        result.append(entry)
    return result


def _make_memory_step(db_path=None, top_k: int = _cfg.top_k) -> RunnableLambda:
    """Return a RunnableLambda that retrieves memories for the prompt.

    Accepts the full pipeline dict and returns {"memories": [...]}.
    RunnableParallel feeds each branch the same input dict, so we extract
    'prompt' here.

    LangChain concept: RunnableLambda wraps any callable as a Runnable,
    giving it .invoke(), .batch(), .stream(), and pipe-operator support.
    """
    retriever = SQLiteMemoryRetriever(
        db_path=str(db_path or _cfg.memory_db),
        top_k=top_k,
    )

    def _run(inputs: dict) -> dict:
        prompt = inputs.get("prompt", "")
        try:
            docs = retriever.invoke(prompt)
        except Exception as exc:
            _log.warning("memory retriever failed: %s", exc)
            docs = []
        return {"memories": _docs_to_dicts(docs)}

    return RunnableLambda(_run)


def _make_tool_hints_step(db_path=None, top_k: int = 5) -> RunnableLambda:
    """Return a RunnableLambda that retrieves tool hints scoped to detected domains.

    LangChain concept: a Runnable can read any key from the input dict,
    not just 'prompt' — here we use both 'prompt' and 'domains'.
    ToolHintsRetriever uses EnsembleRetriever (BM25 + domain filter) internally.
    """
    _db = str(db_path or _cfg.tool_hints_db)

    def _run(inputs: dict) -> dict:
        prompt  = inputs.get("prompt", "")
        domains = inputs.get("domains", [])
        try:
            retriever = ToolHintsRetriever(
                domains=domains,
                db_path=_db,
                top_k=top_k,
            )
            docs = retriever.get_relevant_documents(prompt)
        except Exception as exc:
            _log.warning("tool hints retriever failed: %s", exc)
            docs = []
        return {"tool_hints": _docs_to_dicts(docs)}

    return RunnableLambda(_run)


def _make_merge_step() -> RunnableLambda:
    """Merge the parallel branch outputs with the upstream state dict.

    RunnableParallel returns {"memories": {...}, "tool_hints": {...}} — each
    value is the return of its branch. We flatten into a single MemoryContext.

    LangChain concept: RunnableParallel output is always a dict keyed by the
    branch names you defined. You typically merge it in the next step.
    """

    def _run(inputs: dict) -> MemoryContext:
        memories   = inputs.get("memories", {}).get("memories", [])
        tool_hints = inputs.get("tool_hints", {}).get("tool_hints", [])
        return MemoryContext(
            prompt=inputs.get("prompt", ""),
            domains=inputs.get("domains", []),
            memories=memories,
            tool_hints=tool_hints,
        )

    return RunnableLambda(_run)


# ---------------------------------------------------------------------------
# Pipeline factory
# ---------------------------------------------------------------------------

def build_memory_pipeline(
    *,
    use_llm: bool = False,
    memory_db=None,
    tool_hints_db=None,
    top_k_memories: int = _cfg.top_k,
    top_k_tools: int = 5,
) -> Any:
    """Assemble and return the full LCEL memory pipeline.

    Args:
        use_llm:         Whether to use Claude Haiku for domain classification.
                         Default False — keyword fallback only (fast, no API cost).
        memory_db:       Override path to MEMORY.sqlite (for tests).
        tool_hints_db:   Override path to tool_hints.sqlite (for tests).
        top_k_memories:  Max memories to retrieve per turn.
        top_k_tools:     Max tool hints to retrieve per turn.

    Returns:
        A compiled LCEL chain (Runnable). Call .invoke({"prompt": "...", "cwd": "..."}).

    LangChain concept: the pipe operator (|) is syntactic sugar for
    chain.pipe(next_step). Each step must accept what the previous one returns.

    Step-by-step:
        1. classify_step    — RunnableLambda: adds "domains" key to dict
        2. parallel_step    — RunnableParallel: runs memory + tool retrieval concurrently
        3. merge_step       — RunnableLambda: flattens parallel output → MemoryContext

    Note on RunnableParallel input: RunnableParallel passes the *upstream dict*
    (output of classify_step) to each branch. Since classify_step uses
    RunnablePassthrough (via make_classifier_runnable), the full input dict
    plus "domains" is available to both branches.
    """
    classify_step = make_classifier_runnable(use_llm=use_llm)

    # RunnableParallel: fan out to two retrievers concurrently.
    # Each branch receives the same dict from classify_step.
    parallel_step = RunnableParallel(
        memories=_make_memory_step(db_path=memory_db, top_k=top_k_memories),
        tool_hints=_make_tool_hints_step(db_path=tool_hints_db, top_k=top_k_tools),
        # pass upstream state through so merge_step can read prompt + domains
        prompt=RunnableLambda(lambda x: x.get("prompt", "")),
        domains=RunnableLambda(lambda x: x.get("domains", [])),
    )

    merge_step = _make_merge_step()

    # LCEL pipe: left | right — builds a SequentialChain internally
    return classify_step | parallel_step | merge_step


# ---------------------------------------------------------------------------
# Convenience entry point
# ---------------------------------------------------------------------------

def run_pipeline(
    prompt: str,
    cwd: str = "",
    *,
    use_llm: bool = False,
    memory_db=None,
    tool_hints_db=None,
) -> MemoryContext:
    """One-shot: build and invoke the memory pipeline for a single prompt.

    Not intended for repeated calls in production — build the pipeline once
    via build_memory_pipeline() and reuse the Runnable across turns.
    """
    pipeline = build_memory_pipeline(
        use_llm=use_llm,
        memory_db=memory_db,
        tool_hints_db=tool_hints_db,
    )
    return pipeline.invoke({"prompt": prompt, "cwd": cwd})
