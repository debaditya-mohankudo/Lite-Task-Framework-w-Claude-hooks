"""SetPromptIdNode — generates a UUID for this turn and writes it to the session row."""
from __future__ import annotations

import uuid
from pathlib import Path

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


class SetPromptIdNode:
    """Generate a fresh prompt_id UUID for this UserPromptSubmit turn.

    Writes the UUID to the session row (single UPDATE) so PreToolUse and
    PostToolUse hooks can read it via db.get_prompt_id(session_id).
    Also returns it in state so downstream nodes can read it without a DB call.

    This is the only DB write in the UserPromptSubmit chain.
    """

    def __call__(self, state: SessionState) -> dict:
        from langchain_learning import session_graph as sg
        from core.db.session_db import SessionDB

        entry("set_prompt_id", state)

        prompt_id  = str(uuid.uuid4())
        session_id = state.get("session_id", "")

        if session_id:
            sessions_db = sg._SESSIONS_DB or Path.home() / ".claude" / "sessions.db"
            if sessions_db.exists():
                try:
                    db = SessionDB.open(sessions_db)
                    db.set_prompt_id(session_id, prompt_id)
                    _log.info("[set_prompt_id] session=%s prompt_id=%s",
                              session_id[:8], prompt_id[:8])
                except Exception as exc:
                    _log.warning("[set_prompt_id] DB write failed: %s", exc)
            else:
                _log.info("[set_prompt_id] no sessions.db yet, state-only prompt_id=%s", prompt_id[:8])

        return {"prompt_id": prompt_id}
