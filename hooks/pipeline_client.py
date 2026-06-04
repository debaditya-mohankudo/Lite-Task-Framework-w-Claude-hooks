"""HTTP client for the LangServe pipeline server (Process 1, port 8766).

Hooks call `invoke_pipeline()` — it tries the HTTP server first and falls back
to the in-process pipeline if the server is unreachable or returns an error.

This keeps hooks working even when the server isn't running (dev mode, CI, etc).
"""
from __future__ import annotations

import sys
import urllib.error
import urllib.request
import json
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from src.logger import get_logger

_log = get_logger(__name__)

PIPELINE_URL = "http://127.0.0.1:8766/run"
TIMEOUT_SECS = 3  # fast fallback — hooks are latency-sensitive


def _http_invoke(prompt: str, session_id: str, turn: int) -> dict | None:
    """POST to /run. Returns parsed dict on success, None on any failure."""
    payload = json.dumps({"prompt": prompt, "session_id": session_id, "turn": turn}).encode()
    req = urllib.request.Request(
        PIPELINE_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECS) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        _log.debug("pipeline server unreachable: %s — using in-process fallback", exc)
        return None
    except Exception as exc:
        _log.warning("pipeline server error: %s — using in-process fallback", exc)
        return None


def _inprocess_invoke(prompt: str, session_id: str, turn: int) -> dict:
    """Run the LangGraph graph in-process (no server required)."""
    from langchain_learning.session_graph import run_session
    return run_session(prompt=prompt, session_id=session_id, turn=turn)


def invoke_pipeline(prompt: str, session_id: str = "", turn: int = 0) -> dict:
    """Invoke the memory pipeline. HTTP server preferred; falls back to in-process.

    Returns a dict with keys matching SessionState:
        prompt, session_id, turn, memories, session_context,
        domains, keywords, tool_hints, skip_tools
    """
    result = _http_invoke(prompt, session_id, turn)
    if result is not None:
        _log.debug("pipeline: HTTP response (session=%s turn=%s→%s)", session_id, turn, result.get("turn"))
        return result

    _log.info("pipeline: falling back to in-process (session=%s)", session_id)
    return _inprocess_invoke(prompt, session_id, turn)
