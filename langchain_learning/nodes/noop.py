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

# task:b3964f85 — MemorySaver (which replaced SqliteSaver after two corruption
# incidents) has no built-in eviction: without this cap, a long-running
# session's per-thread checkpoint history now grows unboundedly in RAM instead
# of unboundedly on disk. checkpoint_ids are monotonically sortable strings
# (a LangGraph invariant), so keeping the lexicographically-largest
# _CHECKPOINT_ROW_CAP ids per (thread_id, checkpoint_ns) keeps the most recent
# history and evicts the oldest — costs time-travel/resume-from-old-turn
# ability for evicted checkpoints, never current-state correctness.
_CHECKPOINT_ROW_CAP = 5000


def _trim_thread_checkpoints(thread_id: str, row_cap: int = _CHECKPOINT_ROW_CAP) -> None:
    """Cap this thread's checkpoint history under the live MemorySaver.

    No-ops if the live graph/checkpointer isn't set (e.g. standalone/test
    invocations using get_session_graph()'s own throwaway MemorySaver with no
    prior history) or isn't a MemorySaver (defensive — this reaches into
    MemorySaver-specific internals, .storage/.writes, that no other
    checkpointer implementation exposes the same way).
    """
    import langchain_learning.session_graph as sg
    from langgraph.checkpoint.memory import MemorySaver

    graph = sg._graph
    if graph is None:
        return
    checkpointer = graph.checkpointer
    if not isinstance(checkpointer, MemorySaver):
        return

    thread_storage = checkpointer.storage.get(thread_id)
    if not thread_storage:
        return

    for ns, ns_checkpoints in thread_storage.items():
        if len(ns_checkpoints) <= row_cap:
            continue
        ids_sorted = sorted(ns_checkpoints.keys())  # oldest first
        evict_ids = ids_sorted[:-row_cap]
        for cid in evict_ids:
            del ns_checkpoints[cid]
            checkpointer.writes.pop((thread_id, ns, cid), None)
        _log.info(
            "[noop] checkpoint trim: thread=%s ns=%r evicted=%d kept=%d",
            thread_id[:8], ns, len(evict_ids), row_cap,
        )


class NoopNode:
    """No-op node routed to for stop events and unrecognised event types.

    On the first Stop event of a turn, blocks the stop once to have Claude
    play a completion sound (mcp__local-mac__time__play_sound) — see
    task-notification "cosmetic changes for long running tasks". Every
    subsequent Stop for the same turn (i.e. the one after Claude complies)
    is truly silent, guarded by stop_alert_sent.

    Also caps this thread's checkpoint history (task:b3964f85) on every Stop,
    not just the first of a turn — MemorySaver has no built-in eviction, and
    Stop is the one event guaranteed to fire every turn regardless of
    stop_alert_sent state, mirroring how UserPromptSubmit's cross-session trim
    runs on every prompt rather than only once.

    Tags: fallback, event-routing, noop, sound-alert, checkpoint-trim
    """

    def __call__(self, state: SessionState) -> dict:
        ev = state.get("event_type")
        if ev not in _SILENT_EVENTS:
            _log.warning("[noop] unknown event_type=%r session=%s",
                         ev, (state.get("session_id") or "")[:8])
            return {}

        session_id = state.get("session_id") or ""
        if session_id:
            _trim_thread_checkpoints(session_id)

        if state.get("stop_alert_sent"):
            return {}

        _log.info("[noop] stop sound-alert fired session=%s", session_id[:8])
        return {
            "stop_alert_sent": True,
            "pending_hook_output": {
                "decision": "block",
                "reason": _SOUND_ALERT_REASON,
            },
        }
