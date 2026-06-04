"""Shared node entry logging helper."""
from __future__ import annotations

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
