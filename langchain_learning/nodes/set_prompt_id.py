"""SetPromptIdNode — generates a UUID for this turn and resets prompt tracking state."""
from __future__ import annotations

import uuid

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class SetPromptIdNode:
    """Generate a fresh prompt_id UUID for this UserPromptSubmit turn.

    Resets prompt_tools to [] so gate_check only sees tools called this prompt.
    prompt_id and prompt_tools flow entirely via LangGraph checkpoint state.
    """

    def __call__(self, state: SessionState) -> dict:
        entry("set_prompt_id", state)
        prompt_id = str(uuid.uuid4())
        _log.info("[set_prompt_id] prompt_id=%s", prompt_id[:8])
        return {"prompt_id": prompt_id, "prompt_tools": [], "turn": state.get("turn", 0) + 1}
