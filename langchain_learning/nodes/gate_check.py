"""GateCheckNode — enforces send-gate policy for PreToolUse events."""
from __future__ import annotations

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
        from hooks.gates import check as _gate_check

        tool_name         = state.get("tool_name", "")
        tool_input        = state.get("tool_input") or {}
        prompt_tools: list = list(state.get("prompt_tools") or [])
        prompt_id         = state.get("prompt_id", "")

        entry("gate_check", state, prompt_id=prompt_id[:8] if prompt_id else "?")

        if not tool_name:
            return {"gate_denied": False, "gate_reason": ""}

        def _prompt_had(prereq: str) -> bool:
            return any(
                (isinstance(t, dict) and t.get("tool") == prereq)
                or (isinstance(t, str) and t == prereq)
                for t in prompt_tools
            )

        deny, reason = _gate_check(
            tool_name,
            _prompt_had,
            tool_input,
        )

        if deny:
            _log.warning("[gate_check] DENY tool=%s prompt_id=%s reason=%s", tool_name, prompt_id[:8], reason)
        else:
            _log.info("[gate_check] ALLOW tool=%s prompt_id=%s", tool_name, prompt_id[:8])

        return {"gate_denied": deny, "gate_reason": reason}
