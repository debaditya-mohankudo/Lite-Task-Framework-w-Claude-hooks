"""Pre-tool gate pipeline — LCEL variant.

LangChain concepts demonstrated:
  - RunnableLambda     wrap plain functions as typed pipeline steps
  - RunnableParallel   fan-out: run gate check + passthrough concurrently
  - pipe operator (|)  compose steps left-to-right
  - TypedDict          typed input/output contracts between steps

Pipeline shape:

    hook_input (dict)
        │
    [parse_input]          RunnableLambda — extract + validate fields
        │                  returns _SKIP sentinel dict if not a gated MCP tool
        ▼
    [RunnableParallel]
      ├── gate             RunnableLambda — gates.check → {deny, reason}
      └── passthrough      RunnableLambda — carry fields forward
        │
        ▼
    [format_output]        RunnableLambda — emit hookSpecificOutput or {}

The _SKIP sentinel short-circuits the pipeline: parse_input returns it when
the tool should not be gated (non-MCP, memory tools, etc.). format_output
detects the sentinel and returns {} (allow) without inspecting gate result.

Why RunnableParallel here even with one real branch?
  - Consistency with memory pipeline pattern (easy to add branches later)
  - Passthrough makes the parsed fields available to format_output alongside
    the gate result, without re-parsing
  - Demonstrates the fan-out pattern for learning purposes
"""
from __future__ import annotations

from typing import Any, TypedDict

from langchain_core.runnables import RunnableLambda, RunnableParallel

from src.logger import get_logger

_log = get_logger(__name__)

# Sentinel dict — parse_input returns this to short-circuit non-gated tools
_SKIP = {"skip": True}


# ---------------------------------------------------------------------------
# Typed contracts
# ---------------------------------------------------------------------------

class ParsedInput(TypedDict):
    tool_name: str        # original full tool name (mcp__local-mac__imessage__send)
    short_name: str       # stripped (imessage__send)
    session_id: str
    prompt_id: str
    skip: bool            # True → pipeline should pass through without gating


class GateResult(TypedDict):
    deny: bool
    reason: str


# ---------------------------------------------------------------------------
# Step factories
# ---------------------------------------------------------------------------

def make_parse_step(cfg, strip_mcp_prefix_fn) -> RunnableLambda:
    """Return a RunnableLambda that extracts and validates hook input fields.

    Returns _SKIP when:
      - tool_name is missing or not an MCP tool
      - session_id is missing
      - short_name resolves to a memory__ tool (internal, never gated)

    LangChain concept: RunnableLambda wraps any callable — here we capture
    cfg and strip_mcp_prefix_fn via closure so the step is self-contained.
    """
    def _run(hook_input: dict) -> ParsedInput | dict:
        tool_name  = hook_input.get("tool_name", "")
        session_id = hook_input.get("session_id", "")

        if not tool_name or not session_id or not tool_name.startswith("mcp__"):
            _log.debug("parse_input: skipping non-MCP tool=%r session=%r", tool_name, session_id)
            return _SKIP

        short_name = strip_mcp_prefix_fn(tool_name)
        if not short_name or short_name.startswith("memory__"):
            _log.debug("parse_input: skipping memory tool=%r", tool_name)
            return _SKIP

        prompt_id_tmp = cfg.prompt_id_tmp
        prompt_id = (
            (prompt_id_tmp.read_text().strip() if prompt_id_tmp.exists() else "")
            or hook_input.get("tool_use_id", "")
            or hook_input.get("prompt_id", "")
        )

        return ParsedInput(
            tool_name=tool_name,
            short_name=short_name,
            session_id=session_id,
            prompt_id=prompt_id,
            skip=False,
        )

    return RunnableLambda(_run)


def make_gate_step(gate_check_fn, sessions_db_getter, SessionDB) -> RunnableLambda:
    """Return a RunnableLambda that runs the gate check for a parsed tool call.

    Accepts ParsedInput (or _SKIP — handled by format_output, not here).

    gate_check_fn signature:   (short_name, prompt_had_fn) -> (deny, reason)
    sessions_db_getter:        callable() -> Path  — called at invoke time so
                               that tests can patch the module-level variable
                               and have it reflected here without rebuilding
                               the pipeline.
    SessionDB.open(path).prompt_had_tool(prompt_id, prereq) is the fact source.

    LangChain concept: lazy dependency resolution via getter callable — the
    step captures a reference to a getter rather than a value, so runtime
    state changes (e.g. test patches) are picked up at each .invoke() call.
    """
    def _run(inputs: dict) -> GateResult:
        if inputs.get("skip"):
            return GateResult(deny=False, reason="")

        short_name = inputs["short_name"]
        prompt_id  = inputs["prompt_id"]

        db = SessionDB.open(sessions_db_getter())
        deny, reason = gate_check_fn(
            short_name,
            lambda prereq: db.prompt_had_tool(prompt_id, prereq),
        )
        return GateResult(deny=deny, reason=reason)

    return RunnableLambda(_run)


def make_format_step() -> RunnableLambda:
    """Return a RunnableLambda that converts pipeline output → hook response.

    Receives the merged dict from RunnableParallel:
      {
        "gate":        GateResult,
        "parsed":      ParsedInput | _SKIP,
      }

    Returns hookSpecificOutput deny block on deny, {} (allow) otherwise.

    LangChain concept: terminal formatting step — converts internal types to
    the wire format expected by the caller (Claude Code hook output schema).
    """
    def _run(inputs: dict) -> dict:
        parsed = inputs.get("parsed", _SKIP)
        gate   = inputs.get("gate", GateResult(deny=False, reason=""))

        if parsed.get("_skip") or not gate["deny"]:
            if not parsed.get("_skip"):
                _log.info("ALLOW %s (prompt_id=%s)", parsed.get("short_name"), parsed.get("prompt_id"))
            return {}

        _log.warning(
            "DENY %s (prompt_id=%s): %s",
            parsed.get("short_name"), parsed.get("prompt_id"), gate["reason"],
        )
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": gate["reason"],
            }
        }

    return RunnableLambda(_run)


# ---------------------------------------------------------------------------
# Pipeline factory
# ---------------------------------------------------------------------------

def build_pre_tool_pipeline(cfg, strip_mcp_prefix_fn, gate_check_fn, SessionDB, sessions_db_getter=None) -> Any:
    """Assemble and return the full LCEL pre-tool gate pipeline.

    Args:
        cfg:                   project config (needs .prompt_id_tmp)
        strip_mcp_prefix_fn:   callable(tool_name) → short_name
        gate_check_fn:         callable(short_name, prompt_had_fn) → (deny, reason)
        SessionDB:             class with .open(path) → db instance
        sessions_db_getter:    optional callable() → Path; defaults to lambda: cfg.sessions_db
                               Pass a getter that reads a patchable module variable for tests.

    Returns a compiled LCEL chain. Call .invoke(hook_input_dict).

    Pipeline steps:
        1. parse_step    — validate + extract fields (emits _SKIP for non-gated tools)
        2. parallel_step — RunnableParallel: gate check + passthrough
        3. format_step   — assemble hook output

    LangChain concept: build_* factory pattern — construct once, reuse across
    multiple .invoke() calls. Dependencies injected, never imported globally,
    making the pipeline independently testable.
    """
    db_getter = sessions_db_getter or (lambda: cfg.sessions_db)

    parse_step = make_parse_step(cfg, strip_mcp_prefix_fn)

    parallel_step = RunnableParallel(
        gate=make_gate_step(gate_check_fn, db_getter, SessionDB),
        parsed=RunnableLambda(lambda x: x),  # passthrough parsed fields
    )

    format_step = make_format_step()

    return parse_step | parallel_step | format_step
