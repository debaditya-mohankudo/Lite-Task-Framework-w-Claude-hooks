"""Component 6 — HookRunnable: wrapping a stdin/stdout subprocess as a LangChain Runnable.

LangChain concept: RunnableLambda wrapping an external process.

Any program that speaks JSON on stdin and stdout can become a typed Runnable.
The pattern:
  1. Serialize input dict → JSON → subprocess stdin
  2. Run the subprocess (here: hooks/memory_loader_lc.py)
  3. Parse stdout JSON → return output dict

This gives the external process .invoke(), .batch(), .stream(), and | pipe support
for free — the same interface as ChatAnthropic, a retriever, or any other Runnable.

Primary use: e2e testing memory_loader_lc.py exactly as Claude Code fires it —
  - real MEMORY.sqlite
  - real tool_hints.sqlite
  - real subprocess invocation
  - parsed additionalSystemPrompt from stdout

Usage:
    from langchain_learning.prompt_hook_runnable import build_prompt_hook_runnable

    hook = build_prompt_hook_runnable()
    result = hook.invoke({"prompt": "what is my nakshatra today", "cwd": "/some/path"})
    # result: {"additionalSystemPrompt": "# Active domains: astrology\\n..."}
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from langchain_core.runnables import RunnableLambda
from src.logger import get_logger

_log = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).parent.parent
_HOOK_SCRIPT   = _PROJECT_ROOT / "hooks" / "memory_loader_lc.py"


def _invoke_hook(inputs: dict[str, Any]) -> dict[str, Any]:
    """Run memory_loader_lc.py as a subprocess, return parsed stdout.

    Mirrors exactly how Claude Code fires UserPromptSubmit hooks:
      - stdin: JSON-encoded hook input
      - stdout: JSON-encoded hook output (additionalSystemPrompt, etc.)
      - CLAUDE_CWD env var: current working directory

    Returns the inner hookSpecificOutput dict, or {} on empty/error.
    """
    hook_input = {
        "prompt": inputs.get("prompt", ""),
        "message": inputs.get("message", {}),
    }
    cwd = inputs.get("cwd", str(_PROJECT_ROOT))

    env = {**os.environ, "CLAUDE_CWD": cwd}

    _log.debug("invoking hook script=%s prompt=%r", _HOOK_SCRIPT.name, hook_input["prompt"][:60])

    result = subprocess.run(
        [sys.executable, str(_HOOK_SCRIPT)],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )

    if result.returncode != 0:
        _log.warning("hook exited %d stderr=%s", result.returncode, result.stderr[:200])

    if not result.stdout.strip():
        _log.debug("hook produced no output")
        return {}

    try:
        output = json.loads(result.stdout.strip())
    except json.JSONDecodeError as exc:
        _log.error("failed to parse hook stdout: %s", exc)
        return {}

    # Claude Code hook output shape: {"hookSpecificOutput": {"additionalSystemPrompt": "..."}}
    # Unwrap one level for ergonomic pipeline use
    return output.get("hookSpecificOutput", output)


def build_prompt_hook_runnable() -> RunnableLambda:
    """Return a RunnableLambda that invokes memory_loader_lc.py as a subprocess.

    Input dict keys:
        prompt (str): user prompt text
        cwd    (str, optional): working directory passed as CLAUDE_CWD

    Output dict keys:
        additionalSystemPrompt (str): formatted memory + tool hints block
        (empty dict if hook produced no output)

    LangChain concept: RunnableLambda wrapping an external process.
    The hook becomes composable — e.g. hook | RunnableLambda(extract_prompt).
    """
    return RunnableLambda(_invoke_hook)
