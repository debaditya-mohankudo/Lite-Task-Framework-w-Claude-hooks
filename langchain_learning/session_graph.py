"""Component 2 — Session State Machine.

LangChain concept: LangGraph StateGraph
Replaces: server/core/session_store.py (lifecycle state machine portion)

LangGraph models computation as a directed graph where:
  - State  = a TypedDict snapshot passed through every node
  - Node   = a plain Python function that receives state and returns a partial update
  - Edge   = wiring that says "after node X, go to node Y"
  - Conditional edge = a function that inspects state and returns the next node name

Why StateGraph over plain Python classes?
  - State is immutable between nodes — no shared mutable dicts.
  - Branching logic lives in the graph topology, not inside nodes.
  - Nodes are composable: swap one without touching others.
  - LangGraph handles streaming, checkpointing, and async natively.

Graph shape:
                     ┌──────────────┐
                     │    START     │
                     └──────┬───────┘
                            │
                    ┌───────▼────────┐
                    │  load_memories │  ← scores MEMORY.sqlite keywords
                    └───────┬────────┘
                            │
               ┌────────────▼──────────────┐
               │  load_session_context     │  ← top-2 session_summaries by keyword
               └────────────┬──────────────┘
                            │
                   ┌────────▼─────────┐
                   │ classify_domain  │  ← detects active domains
                   └────────┬─────────┘
                            │
                   ┌────────▼─────────┐
                   │   score_tools    │  ← retrieves tool hints
                   └────────┬─────────┘
                            │
                  ┌─────────▼──────────┐
                  │  persist_session   │  ← writes to sessions.db
                  └─────────┬──────────┘
                             │
                          ┌──▼──┐
                          │ END │
                          └─────┘

Conditional variant: after classify_domain, route to score_tools only when
at least one non-global domain was detected; otherwise skip straight to persist.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Annotated, TypedDict, Sequence

from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages

from langchain_learning.config import config as _cfg
from langchain_learning.logger import get_logger
_log = get_logger(__name__)

# ---------------------------------------------------------------------------
# State definition
# ---------------------------------------------------------------------------
# TypedDict is the LangGraph contract: every key is a typed field.
# Nodes return a dict with a *subset* of these keys — LangGraph merges
# the partial update into the running state automatically.
#
# Annotated[list, add_messages] is a LangGraph reducer:
#   - Plain list fields are replaced on each node update.
#   - add_messages-annotated lists are *appended* (idempotent de-dup by id).
# We use plain lists here because memories/tools are rebuilt each turn.
# ---------------------------------------------------------------------------

class SessionState(TypedDict):
    prompt: str                    # raw prompt text from this turn
    session_id: str                # Claude Code session identifier
    turn: int                      # monotonically increasing turn counter
    memories: list[dict]           # scored memory rows injected this turn
    session_context: str           # formatted session_summaries snippet (top 2 by keyword match)
    domains: list[str]             # detected active domains (e.g. ["macos", "vault"])
    keywords: list[str]            # extracted prompt keywords
    tool_hints: list[dict]         # retrieved tool hint rows
    skip_tools: bool               # set by classify_domain if no domain detected


# ---------------------------------------------------------------------------
# Node implementations
# ---------------------------------------------------------------------------

def load_memories(state: SessionState) -> dict:
    """Score MEMORY.sqlite rows against current prompt keywords.

    LangGraph concept: a node is just a function — receives full state,
    returns a partial dict with only the keys it updates.

    Scoring: keyword overlap between prompt tokens and memory body/tags.
    Priority-1 memories (always-inject) are included unconditionally.
    """
    prompt = state["prompt"].lower()
    tokens = set(_tokenise(prompt))

    if not _cfg.memory_db.exists():
        _log.warning("MEMORY.sqlite not found at %s", _cfg.memory_db)
        return {"memories": [], "keywords": list(tokens)}

    scored: list[tuple[float, dict]] = []
    try:
        conn = sqlite3.connect(f"file:{_cfg.memory_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name, type, domain, priority, tags, body FROM memories"
        ).fetchall()
        conn.close()
    except Exception as exc:
        _log.error("load_memories DB error: %s", exc)
        return {"memories": [], "keywords": list(tokens)}

    for row in rows:
        if row["priority"] == 1:
            scored.append((1.0, dict(row)))
            continue
        haystack = f"{row['tags'] or ''} {row['body'] or ''}".lower()
        overlap = sum(1 for t in tokens if t in haystack)
        if overlap > 0:
            scored.append((overlap / max(len(tokens), 1), dict(row)))

    scored.sort(key=lambda x: (-x[0], x[1].get("priority", 50)))
    return {
        "memories": [m for _, m in scored[:10]],
        "keywords": list(tokens),
    }


def load_session_context(state: SessionState) -> dict:
    """Keyword-search session_summaries and return top-2 as a formatted string.

    Mirrors server/core/scorer.py::_retrieve_session_context from the old implementation.
    Tags are weighted 3×, summary body 1×. Result is stored in state["session_context"]
    so the prompt assembler (in the FastAPI layer) can append it as "# Session Context".
    """
    keywords = set(state.get("keywords") or [])
    if not keywords:
        return {"session_context": ""}

    sessions_db = _SESSIONS_DB if _SESSIONS_DB is not None else Path.home() / ".claude" / "sessions.db"
    if not sessions_db.exists():
        return {"session_context": ""}

    try:
        with sqlite3.connect(f"file:{sessions_db}?mode=ro", uri=True) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT session_id, summary, tags FROM session_summaries"
            ).fetchall()
    except Exception as exc:
        _log.error("load_session_context DB error: %s", exc)
        return {"session_context": ""}

    def _score(row) -> int:
        tag_hits  = sum(3 for t in (row["tags"] or "").split(",") if t.strip() in keywords)
        body_hits = sum(1 for w in row["summary"].lower().split() if w.strip(".,;:") in keywords)
        return tag_hits + body_hits

    scored = sorted(rows, key=_score, reverse=True)
    top2   = [r for r in scored[:2] if _score(r) > 0]
    if not top2:
        return {"session_context": ""}

    lines = []
    for r in top2:
        tag_hint = ", ".join(t.strip() for t in (r["tags"] or "").split(",") if t.strip())[:80]
        preview  = (r["summary"] or "")[:200]
        lines.append(f"- [{r['session_id'][:8]}] ({tag_hint}): {preview}")

    return {"session_context": "\n".join(lines)}


def classify_domain(state: SessionState) -> dict:
    """Detect which domains are active based on keyword overlap.

    LangGraph concept: nodes can set control-flow flags in state.
    Here we set `skip_tools=True` when no specific domain is found —
    the conditional edge downstream reads this to skip score_tools.

    Domain signals (checked in order):
      1. Explicit domain keyword in prompt (e.g. "gold", "nakshatra")
      2. Memory domain of top-scored memories from previous node
    """
    keywords = set(state["keywords"])
    memories = state["memories"]

    # domain keyword vocab — lightweight, no LLM call needed here
    _DOMAIN_VOCAB: dict[str, set[str]] = {
        "astrology":    {"nakshatra", "panchang", "rahu", "ketu", "dasha", "tithi", "lagna", "graha", "jyotish"},
        "market-intel": {"gold", "nifty", "sensex", "fii", "dii", "market", "stock", "equity", "portfolio"},
        "vault":        {"vault", "note", "write", "document", "save", "capture"},
        "macos":        {"message", "calendar", "contact", "reminder", "mail", "imessage", "safari", "music"},
        "health":       {"health", "sleep", "exercise", "weight", "calories", "heart"},
        "philosophy":   {"philosophy", "vedanta", "advaita", "consciousness", "brahman"},
        "coding-best-practices": {"python", "code", "function", "class", "test", "async", "typing"},
    }

    detected: set[str] = set()

    # signal 1: keyword match
    for domain, vocab in _DOMAIN_VOCAB.items():
        if keywords & vocab:
            detected.add(domain)

    # signal 2: top memory domains (max 3 memories)
    for mem in memories[:3]:
        d = mem.get("domain", "global")
        if d and d != "global" and d in _cfg.valid_domains:
            detected.add(d)

    domains = sorted(detected)
    _log.debug("classify_domain: domains=%s skip_tools=%s", domains, len(domains) == 0)
    return {
        "domains": domains,
        "skip_tools": len(domains) == 0,
    }


def score_tools(state: SessionState) -> dict:
    """Retrieve relevant tool hints from tool_hints.sqlite.

    LangGraph concept: a node that performs IO and returns structured data.
    Skipped entirely when classify_domain sets skip_tools=True (no domain).

    Scoring: domain match + keyword overlap on tool keywords column.
    Returns top-5 tools sorted by composite score.
    """
    domains = set(state["domains"])
    keywords = set(state["keywords"])

    if not _cfg.tool_hints_db.exists():
        _log.warning("tool_hints.sqlite not found at %s", _cfg.tool_hints_db)
        return {"tool_hints": []}

    try:
        conn = sqlite3.connect(f"file:{_cfg.tool_hints_db}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT tool_name, domain, skill, count, keywords FROM mcp_tool_hints"
        ).fetchall()
        conn.close()
    except Exception as exc:
        _log.error("score_tools DB error: %s", exc)
        return {"tool_hints": []}

    scored: list[tuple[float, dict]] = []
    for row in rows:
        domain_match = 1.0 if row["domain"] in domains else 0.0
        kw_overlap = sum(1 for k in keywords if k in (row["keywords"] or ""))
        score = domain_match * 2 + kw_overlap
        if score > 0:
            scored.append((score, {
                "tool_name": row["tool_name"],
                "domain":    row["domain"],
                "skill":     row["skill"] or "",
                "count":     row["count"] or 0,
            }))

    scored.sort(key=lambda x: -x[0])
    hints = [h for _, h in scored[:5]]
    _log.debug("score_tools: domains=%s returned=%d tools", list(domains), len(hints))
    return {"tool_hints": hints}


_SESSIONS_DB: Path | None = None  # injectable for tests; None = auto-detect


def persist_session(state: SessionState) -> dict:
    """Write session state snapshot to sessions.db.

    LangGraph concept: a terminal node that performs side effects.
    Returns incremented turn — the only state mutation at this step.

    Writes to the sessions table (upsert by session_id). Does NOT write
    session_summaries — that's the job of the session-compact-persist skill.
    """
    session_id = state["session_id"]
    if not session_id:
        return {"turn": state["turn"] + 1}

    sessions_db = _SESSIONS_DB if _SESSIONS_DB is not None else Path.home() / ".claude" / "sessions.db"
    if not sessions_db.exists():
        return {"turn": state["turn"] + 1}

    import json
    new_turn = state["turn"] + 1

    try:
        with sqlite3.connect(str(sessions_db)) as conn:
            existing = conn.execute(
                "SELECT session_id FROM sessions WHERE session_id = ?", (session_id,)
            ).fetchone()

            domains_json  = json.dumps(state["domains"])
            keywords_json = json.dumps(state["keywords"])

            if existing:
                conn.execute(
                    """UPDATE sessions
                       SET keywords=?, domains=?, turn=?, updated_at=datetime('now')
                       WHERE session_id=?""",
                    (keywords_json, domains_json, new_turn, session_id),
                )
            else:
                conn.execute(
                    """INSERT INTO sessions (session_id, keywords, domains, turn, updated_at)
                       VALUES (?, ?, ?, ?, datetime('now'))""",
                    (session_id, keywords_json, domains_json, new_turn),
                )
            conn.commit()
    except Exception:
        pass

    return {"turn": new_turn}


# ---------------------------------------------------------------------------
# Conditional edge — skip score_tools when no domain detected
# ---------------------------------------------------------------------------

def _route_after_classify(state: SessionState) -> str:
    """Return the next node name based on state.

    LangGraph concept: conditional edges are just functions that return
    a string node name. The graph uses this to pick the next step at runtime.
    The return values must match keys in the routing map passed to
    add_conditional_edges().
    """
    return "skip_tools" if state["skip_tools"] else "score_tools"


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------

def build_session_graph() -> StateGraph:
    """Construct and compile the session processing graph.

    LangGraph concept: StateGraph compilation validates the graph
    (unreachable nodes, missing edges) and returns a Runnable — it
    can be invoked like any LCEL chain: graph.invoke(state_dict).

    Two paths through the graph:
      Prompt with domain signal:  START → load_memories → load_session_context → classify_domain → score_tools → persist_session → END
      Prompt with no domain:      START → load_memories → load_session_context → classify_domain → persist_session → END
    """
    builder = StateGraph(SessionState)

    # add nodes (name, function)
    builder.add_node("load_memories",         load_memories)
    builder.add_node("load_session_context",  load_session_context)
    builder.add_node("classify_domain",       classify_domain)
    builder.add_node("score_tools",           score_tools)
    builder.add_node("persist_session",       persist_session)

    # fixed edges
    builder.add_edge(START,                    "load_memories")
    builder.add_edge("load_memories",          "load_session_context")
    builder.add_edge("load_session_context",   "classify_domain")
    builder.add_edge("score_tools",     "persist_session")
    builder.add_edge("persist_session", END)

    # conditional edge: classify_domain → score_tools OR persist_session
    builder.add_conditional_edges(
        "classify_domain",
        _route_after_classify,
        {
            "score_tools":    "score_tools",
            "skip_tools":     "persist_session",
        },
    )

    return builder.compile()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_graph = None  # module-level singleton


def get_session_graph():
    """Return compiled graph (singleton — compiled once per process)."""
    global _graph
    if _graph is None:
        _graph = build_session_graph()
    return _graph


def run_session(prompt: str, session_id: str = "", turn: int = 0) -> SessionState:
    """Convenience entry point: run the full graph for one prompt turn.

    Returns the final SessionState after all nodes have executed.
    """
    graph = get_session_graph()
    initial: SessionState = {
        "prompt":     prompt,
        "session_id": session_id,
        "turn":       turn,
        "memories":        [],
        "session_context": "",
        "domains":         [],
        "keywords":        [],
        "tool_hints":      [],
        "skip_tools":      False,
    }
    return graph.invoke(initial)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _tokenise(text: str) -> list[str]:
    """Extract lowercase alpha tokens, 3+ chars, from text."""
    import re
    return [t for t in re.findall(r"[a-z]{3,}", text.lower()) if t]
