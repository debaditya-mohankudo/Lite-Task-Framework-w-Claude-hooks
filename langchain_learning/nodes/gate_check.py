"""GateCheckNode — enforces send-gate policy for PreToolUse events."""
from __future__ import annotations

from collections import OrderedDict

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class GateCheckNode:
    """Run gate policy against the tool call.

    Prepares a GateContext from SessionState and dispatches to the matching
    Gate subclass. Each gate owns its own verification logic.

    Fail-open: returns gate_denied=False on any error.

    Tags: gate, pre-tool-use, tool-policy, gate-denied, security
    """

    def __call__(self, state: SessionState) -> dict:
        from hooks.gates import GateContext, ToolCall, GATES, check as _gate_check

        tool_name  = state.get("tool_name", "")
        tool_input = state.get("tool_input") or {}
        prompt_id  = state.get("prompt_id", "")

        if not tool_name:
            return {"gate_denied": False, "gate_reason": ""}

        if tool_name not in GATES:
            return {"gate_denied": False, "gate_reason": ""}

        # Build rich ToolCall list from prompt_tools (current prompt)
        raw_prompt_tools: list = list(state.get("prompt_tools") or [])
        current_calls: list[ToolCall] = [
            ToolCall(
                tool=t["tool"] if isinstance(t, dict) else t,
                prompt_id=prompt_id,
                tool_input=t.get("tool_input", {}) if isinstance(t, dict) else {},
                tool_result=t.get("tool_result", {}) if isinstance(t, dict) else {},
                found=t.get("found", False) if isinstance(t, dict) else False,
            )
            for t in raw_prompt_tools
        ]

        session_tools: OrderedDict[str, list[dict]] = OrderedDict(state.get("session_tools") or {})
        session_prompt_ids: list[str] = list(state.get("session_prompt_ids") or [])

        ctx = GateContext(
            tool_name=tool_name,
            tool_input=tool_input,
            current_calls=current_calls,
            session_tools=session_tools,
            session_prompt_ids=session_prompt_ids,
            prompt_id=prompt_id,
        )

        entry("gate_check", state, prompt_id=prompt_id[:8] if prompt_id else "?")

        _log.debug(
            "[gate_check] tool=%s current=%s session_depth=%d",
            tool_name,
            [c.tool for c in current_calls],
            len(session_prompt_ids),
        )

        deny, reason = _gate_check(tool_name, ctx)

        if deny:
            _log.warning("[gate_check] DENY tool=%s prompt_id=%s reason=%s",
                         tool_name, prompt_id[:8] if prompt_id else "?", reason)
        else:
            _log.info("[gate_check] ALLOW tool=%s prompt_id=%s",
                      tool_name, prompt_id[:8] if prompt_id else "?")

        return {"gate_denied": deny, "gate_reason": reason}
