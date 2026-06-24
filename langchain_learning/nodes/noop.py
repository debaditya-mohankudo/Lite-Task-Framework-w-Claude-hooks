"""NoopNode — silent pass-through for stop and unknown event types."""
from __future__ import annotations

from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_SILENT_EVENTS = {"stop"}


class NoopNode:
    """No-op node routed to for stop events and unrecognised event types.

    Tags: fallback, event-routing, noop
    """

    def __call__(self, state: SessionState) -> dict:
        ev = state.get("event_type")
        if ev not in _SILENT_EVENTS:
            _log.warning("[noop] unknown event_type=%r session=%s",
                         ev, (state.get("session_id") or "")[:8])
        return {}
