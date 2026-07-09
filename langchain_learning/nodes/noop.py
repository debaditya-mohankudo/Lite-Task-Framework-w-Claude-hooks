"""NoopNode — silent pass-through for stop and unknown event types."""
from __future__ import annotations

from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)

_SILENT_EVENTS = {"stop"}

# Fed back to Claude via decision:"block" so it actually calls the tool before
# stopping for real. One-shot per turn — see stop_alert_sent below — otherwise
# the resulting extra Stop event would re-trigger this and loop forever.
_SOUND_ALERT_REASON = (
    "Long-running task finished. Call mcp__local-mac__time__play_sound"
    "(seconds=10) now, then stop."
)


class NoopNode:
    """No-op node routed to for stop events and unrecognised event types.

    On the first Stop event of a turn, blocks the stop once to have Claude
    play a completion sound (mcp__local-mac__time__play_sound) — see
    task-notification "cosmetic changes for long running tasks". Every
    subsequent Stop for the same turn (i.e. the one after Claude complies)
    is truly silent, guarded by stop_alert_sent.

    Tags: fallback, event-routing, noop, sound-alert
    """

    def __call__(self, state: SessionState) -> dict:
        ev = state.get("event_type")
        if ev not in _SILENT_EVENTS:
            _log.warning("[noop] unknown event_type=%r session=%s",
                         ev, (state.get("session_id") or "")[:8])
            return {}

        if state.get("stop_alert_sent"):
            return {}

        _log.info("[noop] stop sound-alert fired session=%s", (state.get("session_id") or "")[:8])
        return {
            "stop_alert_sent": True,
            "pending_hook_output": {
                "decision": "block",
                "reason": _SOUND_ALERT_REASON,
            },
        }
