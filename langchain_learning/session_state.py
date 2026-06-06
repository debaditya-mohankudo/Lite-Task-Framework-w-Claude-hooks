"""SessionState TypedDict — shared across session_graph and all nodes."""
from __future__ import annotations

from collections import OrderedDict
from typing import TypedDict


class SessionState(TypedDict):
    # --- routing ---
    event_type: str          # "user_prompt_submit" | "pre_tool_use" | "post_tool_use" | "stop"

    # --- common ---
    prompt: str
    cwd: str
    session_id: str
    turn: int

    # --- UserPromptSubmit outputs ---
    memories: list[dict]
    prompt_context: dict[str, str]   # session_id → summary preview
    domains: list[str]
    keywords: list[str]
    tool_hints: list[dict]
    skip_tools: bool
    active_task_id: str              # set via task_activate branch; flows through session via checkpoint
    active_task_title: str           # task title, set alongside active_task_id
    task_memories: list[dict]        # memories scored against task tags+title (task_activate branch)
    task_context: list[dict]         # prior turn events for active task (current session only)

    # --- classify chain intermediate state ---
    classifier_scores: dict          # per-domain raw scores
    matched_keywords: list[str]      # signal tokens that fired

    # --- stop chain ---
    current_state: str               # "prompt" | "stop"

    # --- prompt tracking ---
    prompt_id: str                            # UUID generated each UserPromptSubmit; shared across hook invocations via checkpoint
    prompt_tools: list[str]                   # tool short-names called this prompt (appended by log_tool_usage, reset by set_prompt_id)
    session_prompt_ids: list[str]             # ordered list of all prompt_ids in this session
    session_tools: OrderedDict[str, list[dict]]  # prompt_id → [{"tool": str, "tool_input": dict}]; used by gates for input-aware prev_tools()

    # --- PreToolUse / PostToolUse inputs ---
    tool_name: str
    tool_input: dict

    # --- PreToolUse outputs ---
    gate_denied: bool
    gate_reason: str

    # --- PostToolUse inputs ---
    duration_ms: float
    tool_result: dict                # tool_response from PostToolUse hook input
    # tool_use_id: str  # available in hook input but not consumed by any node

