"""MemoryDomainSignalNode — adds domains from top injected memories."""
from __future__ import annotations

from langchain_learning.config import config as _cfg
from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class MemoryDomainSignalNode:
    """Add domains inferred from top-3 injected memories to state["domains"].

    Memory domains are a soft signal — they reflect what was relevant in prior
    turns, not necessarily the current prompt. Capped at 3 to avoid noise from
    lower-priority memories.
    """

    def __call__(self, state: SessionState) -> dict:
        entry("memory_domain_signal", state, memories=len(state.get("memories", [])))

        memories = state.get("memories", [])
        detected = list(state.get("domains", []))

        added = []
        for mem in memories[:3]:
            d = mem.get("domain", "global")
            if d and d != "global" and d in _cfg.valid_domains and d not in detected:
                detected.append(d)
                added.append(d)

        if added:
            _log.info("[memory_domain_signal] added domains from memories: %s", added)

        return {"domains": detected}
