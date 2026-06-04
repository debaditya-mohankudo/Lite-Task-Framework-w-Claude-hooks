"""NoopNode — fallback for unknown event types."""
from __future__ import annotations

from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class NoopNode:
    """No-op node routed to when event_type is unrecognised."""

    def __call__(self, state: SessionState) -> dict:
        _log.warning("[noop] unknown event_type=%r session=%s",
                     state.get("event_type"), (state.get("session_id") or "")[:8])
        return {}
