"""GateCheckNode — enforces send-gate policy for PreToolUse events."""
from __future__ import annotations

from pathlib import Path

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class GateCheckNode:
    """Run gate policy against the tool call.

    Sets gate_denied=True + gate_reason when a prerequisite tool has not been
    called yet this prompt. Gate rules live in hooks/gates.py.
    Fail-open: returns gate_denied=False on any error.
    """

    def __call__(self, state: SessionState) -> dict:
        from langchain_learning import session_graph as sg
        from hooks.gates import check as _gate_check
        from core.db.session_db import SessionDB

        tool_name  = state.get("tool_name", "")
        tool_input = state.get("tool_input") or {}
        prompt_id  = state.get("prompt_id", "")

        entry("gate_check", state, prompt_id=prompt_id[:8] if prompt_id else "?")

        if not tool_name:
            return {"gate_denied": False, "gate_reason": ""}

        sessions_db = sg._SESSIONS_DB or Path.home() / ".claude" / "sessions.db"
        db = SessionDB.open(sessions_db)
        deny, reason = _gate_check(
            tool_name,
            lambda prereq: db.prompt_had_tool(prompt_id, prereq),
            tool_input,
        )

        if deny:
            _log.warning("[gate_check] DENY tool=%s prompt_id=%s reason=%s", tool_name, prompt_id[:8], reason)
        else:
            _log.info("[gate_check] ALLOW tool=%s prompt_id=%s", tool_name, prompt_id[:8])

        return {"gate_denied": deny, "gate_reason": reason}
