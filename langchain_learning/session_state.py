"""SessionState TypedDict — shared across session_graph and all nodes."""
from __future__ import annotations

from typing import TypedDict


class SessionState(TypedDict):
    # --- routing ---
    event_type: str          # "user_prompt_submit" | "pre_tool_use" | "post_tool_use" | "stop"

    # --- common ---
    prompt: str
    session_id: str
    turn: int

    # --- UserPromptSubmit outputs ---
    memories: list[dict]
    session_context: str
    session_context_ids: list[str]
    domains: list[str]
    keywords: list[str]
    tool_hints: list[dict]
    skip_tools: bool

    # --- PreToolUse / PostToolUse inputs ---
    tool_name: str
    tool_input: dict
    prompt_id: str

    # --- PreToolUse outputs ---
    gate_denied: bool
    gate_reason: str

    # --- PostToolUse inputs ---
    duration_ms: float
    tool_use_id: str
