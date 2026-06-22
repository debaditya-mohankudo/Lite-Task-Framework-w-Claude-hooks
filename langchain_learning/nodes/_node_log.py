"""Shared node entry logging helper."""
from __future__ import annotations

import time
from typing import Callable

from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


def entry(node: str, state: SessionState, **extra) -> None:
    """Log node entry with event_type, session, turn, and any extras."""
    _log.info(
        "[%s] event=%s session=%s turn=%s %s",
        node,
        state.get("event_type", "?"),
        (state.get("session_id") or "")[:8] or "?",
        state.get("turn", "?"),
        " ".join(f"{k}={v}" for k, v in extra.items()),
    )


def wrap(name: str, fn: Callable) -> Callable:
    """Wrap a node callable with → before and ← after timing logs."""
    def _wrapped(state: SessionState) -> dict:
        sid = (state.get("session_id") or "")[:8] or "?"
        _log.debug("→ %s session=%s", name, sid)
        t0 = time.monotonic()
        result = fn(state)
        ms = (time.monotonic() - t0) * 1000
        _log.debug("← %s session=%s %.1fms", name, sid, ms)
        return result
    _wrapped.__name__ = name
    return _wrapped
