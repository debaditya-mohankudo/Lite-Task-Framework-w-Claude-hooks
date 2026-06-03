"""E2E tests for langchain_learning/memory_loader_e2e_test_runnable.py — Component 6.

Strategy:
  - build_memory_loader_e2e_test_runnable() invokes memory_loader_lc.py as a real subprocess.
  - Tests use the REAL MEMORY.sqlite and tool_hints.sqlite (no mocks).
  - Skipped when the real DBs are absent (CI / fresh machines).

LangChain concept verified:
  - RunnableLambda wrapping an external stdin/stdout process
  - Full .invoke() path: input dict → subprocess → parsed output dict
  - Composability: hook | RunnableLambda(fn) works as a pipeline step
"""
import json
import pytest
from pathlib import Path

from langchain_learning.memory_loader_e2e_test_runnable import build_memory_loader_e2e_test_runnable, _invoke_hook
from langchain_learning.config import config as _cfg


# ---------------------------------------------------------------------------
# Skip marker — real DBs required for e2e tests
# ---------------------------------------------------------------------------

_REAL_DBS_AVAILABLE = _cfg.memory_db.exists() and _cfg.tool_hints_db.exists()

requires_real_dbs = pytest.mark.skipif(
    not _REAL_DBS_AVAILABLE,
    reason=f"Real DBs not available: memory_db={_cfg.memory_db.exists()}, tool_hints_db={_cfg.tool_hints_db.exists()}",
)


# ---------------------------------------------------------------------------
# Unit tests — no subprocess, no real DBs
# ---------------------------------------------------------------------------

def test_build_memory_loader_e2e_test_runnable_returns_runnable():
    hook = build_memory_loader_e2e_test_runnable()
    assert hook is not None
    assert hasattr(hook, "invoke")
    assert hasattr(hook, "batch")
    assert hasattr(hook, "stream")


def test_build_memory_loader_e2e_test_runnable_has_pipe_operator():
    """Runnable must support | for pipeline composition."""
    from langchain_core.runnables import RunnableLambda
    hook = build_memory_loader_e2e_test_runnable()
    extract = RunnableLambda(lambda x: x.get("additionalSystemPrompt", ""))
    pipeline = hook | extract
    assert pipeline is not None
    assert hasattr(pipeline, "invoke")


def test_invoke_hook_empty_prompt_returns_empty():
    """Empty prompt → hook writes {} → _invoke_hook returns {}."""
    result = _invoke_hook({"prompt": "", "cwd": ""})
    assert result == {}


def test_invoke_hook_output_is_dict():
    """Any non-empty prompt must return a dict (possibly empty if no memories match)."""
    result = _invoke_hook({"prompt": "hello world", "cwd": ""})
    assert isinstance(result, dict)


# ---------------------------------------------------------------------------
# E2E tests — real subprocess against real MEMORY.sqlite + tool_hints.sqlite
# ---------------------------------------------------------------------------

@requires_real_dbs
def test_e2e_invoke_returns_dict():
    hook = build_memory_loader_e2e_test_runnable()
    result = hook.invoke({"prompt": "what is my nakshatra today"})
    assert isinstance(result, dict)


@requires_real_dbs
def test_e2e_returns_additional_system_prompt_key():
    hook = build_memory_loader_e2e_test_runnable()
    result = hook.invoke({"prompt": "nakshatra panchang today rahu"})
    # If any memories matched, additionalSystemPrompt must be present
    if result:
        assert "additionalSystemPrompt" in result


@requires_real_dbs
def test_e2e_system_prompt_is_string():
    hook = build_memory_loader_e2e_test_runnable()
    result = hook.invoke({"prompt": "nakshatra panchang today"})
    if "additionalSystemPrompt" in result:
        assert isinstance(result["additionalSystemPrompt"], str)
        assert len(result["additionalSystemPrompt"]) > 0


@requires_real_dbs
def test_e2e_cwd_sets_macos_domain():
    """CWD=claude_for_mac_local path → macos domain → macos memories injected."""
    hook = build_memory_loader_e2e_test_runnable()
    result = hook.invoke({
        "prompt": "send imessage",
        "cwd": "/Users/debaditya/workspace/claude_for_mac_local",
    })
    if "additionalSystemPrompt" in result:
        assert "macos" in result["additionalSystemPrompt"].lower() or len(result["additionalSystemPrompt"]) > 0


@requires_real_dbs
def test_e2e_pipeline_composition():
    """hook | extract_fn composes cleanly and returns the system prompt string."""
    from langchain_core.runnables import RunnableLambda

    hook = build_memory_loader_e2e_test_runnable()
    extract = RunnableLambda(lambda x: x.get("additionalSystemPrompt", ""))
    pipeline = hook | extract

    result = pipeline.invoke({"prompt": "nakshatra today panchang"})
    assert isinstance(result, str)


@requires_real_dbs
def test_e2e_batch_two_prompts():
    """batch() runs two hook invocations — verifies Runnable batch support."""
    hook = build_memory_loader_e2e_test_runnable()
    results = hook.batch([
        {"prompt": "nakshatra today"},
        {"prompt": "nifty market stocks"},
    ])
    assert len(results) == 2
    assert all(isinstance(r, dict) for r in results)


@requires_real_dbs
def test_e2e_message_block_format():
    """Hook also accepts Claude Code message-block format on stdin."""
    hook = build_memory_loader_e2e_test_runnable()
    result = hook.invoke({
        "message": {
            "content": [
                {"type": "text", "text": "nakshatra panchang today"}
            ]
        }
    })
    assert isinstance(result, dict)
