"""Shared node entry/exit logging helpers.

Log conventions (readable without reading code):
  → node  session=X          — node started
  ← node  session=X  Nms     — node finished with wall-clock time
  [node]  phase=parallel     — node is running in the fan-out parallel tier
  [node]  phase=sequential   — node is in the sequential part of the pipeline
  UPS     phase=done  Nms    — full user_prompt_submit pipeline elapsed
"""
from __future__ import annotations

import time
from typing import Callable

from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

# Nodes that run in the parallel fan-out tier — update when session_graph changes
_PARALLEL_NODES = frozenset({
    "cwd_domain_detect", "load_memories", "score_tools",
    "load_task_history", "load_task_code", "load_related_tasks",
})


def entry(node: str, state: SessionState, **extra) -> None:
    """Log node entry with phase, event_type, session, turn, and any extras."""
    phase = "parallel" if node in _PARALLEL_NODES else "sequential"
    _log.info(
        "[%s] phase=%s event=%s session=%s turn=%s %s",
        node,
        phase,
        state.get("event_type", "?"),
        (state.get("session_id") or "")[:8] or "?",
        state.get("turn", "?"),
        " ".join(f"{k}={v}" for k, v in extra.items()),
    )


def wrap(name: str, fn: Callable) -> Callable:
    """Wrap a node callable with → / ← timing logs and phase label."""
    phase = "parallel" if name in _PARALLEL_NODES else "sequential"

    def _wrapped(state: SessionState) -> dict:
        sid = (state.get("session_id") or "")[:8] or "?"
        _log.debug("→ %s phase=%s session=%s", name, phase, sid)
        t0 = time.monotonic()
        result = fn(state)
        ms = (time.monotonic() - t0) * 1000
        _log.debug("← %s phase=%s session=%s %.1fms", name, phase, sid, ms)
        return result

    _wrapped.__name__ = name
    return _wrapped
