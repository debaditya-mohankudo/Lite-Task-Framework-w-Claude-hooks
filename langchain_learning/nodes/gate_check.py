"""GateCheckNode — enforces send-gate policy for PreToolUse events."""
from __future__ import annotations

from collections import OrderedDict

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class GateCheckNode:
    """Run gate policy against the tool call.

    Sets gate_denied=True + gate_reason when a prerequisite tool has not been
    called yet this prompt. Gate rules live in hooks/gates.py.

    _prompt_had checks two scopes:
      - current prompt_tools (tools called this turn)
      - previous prompt_id's entry in session_tools (tools called last turn)
    This lets confirm__send called in turn N satisfy the gate for imessage__send in turn N+1.

    Fail-open: returns gate_denied=False on any error.
    """

    def __call__(self, state: SessionState) -> dict:
        from hooks.gates import check as _gate_check

        tool_name    = state.get("tool_name", "")
        tool_input   = state.get("tool_input") or {}
        prompt_tools: list = list(state.get("prompt_tools") or [])
        prompt_id    = state.get("prompt_id", "")

        session_tools: OrderedDict[str, list[str]] = OrderedDict(state.get("session_tools") or {})
        session_prompt_ids: list[str] = list(state.get("session_prompt_ids") or [])

        # Build a flat set of all tools called in this session (across all prompt turns)
        all_session_tools: set[str] = {
            tool
            for tools in session_tools.values()
            for tool in tools
        }

        # Find the prompt_id immediately before the current one
        prev_prompt_id = None
        if prompt_id in session_prompt_ids:
            idx = session_prompt_ids.index(prompt_id)
            if idx > 0:
                prev_prompt_id = session_prompt_ids[idx - 1]
        prev_tools: list[str] = list(session_tools.get(prev_prompt_id, [])) if prev_prompt_id else []

        entry("gate_check", state, prompt_id=prompt_id[:8] if prompt_id else "?",
              prev_prompt_id=prev_prompt_id[:8] if prev_prompt_id else "none")

        _log.debug(
            "[gate_check] scope current=%s prev=%s session=%s depth=%d",
            [t.get("tool") if isinstance(t, dict) else t for t in prompt_tools],
            prev_tools,
            sorted(all_session_tools),
            len(session_prompt_ids),
        )

        if not tool_name:
            return {"gate_denied": False, "gate_reason": ""}

        prereq_results: dict[str, bool] = {}

        def _prompt_had(prereq: str) -> bool:
            in_current = any(
                (isinstance(t, dict) and t.get("tool") == prereq)
                or (isinstance(t, str) and t == prereq)
                for t in prompt_tools
            )
            # confirm__send: must be in the immediately previous prompt (cross-turn UX confirmation)
            # contacts__search: anywhere in the session is sufficient (anti-hallucination lookup)
            if prereq == "confirm__send":
                result = in_current or prereq in prev_tools
            else:
                result = in_current or prereq in all_session_tools
            prereq_results[prereq] = result
            return result

        deny, reason = _gate_check(tool_name, _prompt_had, tool_input)

        if deny:
            _log.warning("[gate_check] DENY tool=%s prompt_id=%s prev=%s prereqs=%s reason=%s",
                         tool_name, prompt_id[:8], prev_prompt_id[:8] if prev_prompt_id else "none",
                         prereq_results, reason)
        else:
            _log.info("[gate_check] ALLOW tool=%s prompt_id=%s prev=%s prereqs=%s",
                      tool_name, prompt_id[:8], prev_prompt_id[:8] if prev_prompt_id else "none",
                      prereq_results)

        return {"gate_denied": deny, "gate_reason": reason}
