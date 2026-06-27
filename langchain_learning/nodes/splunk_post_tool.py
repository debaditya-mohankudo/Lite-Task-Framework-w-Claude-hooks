"""SplunkPostToolNode — PostToolUse node for splunk__ MCP tools.

Fires after splunk__submit_report and splunk__investigate_start.

- splunk__investigate_start: records run_id in session state.
- splunk__submit_report: extracts next findings from tool result and injects
  them into additionalSystemPrompt so Claude sees them on the next turn
  without any manual curl calls.

Tags: splunk, post-tool-use, investigation, findings, additionalSystemPrompt
"""
from __future__ import annotations

import json

from langchain_learning.nodes._node_log import entry
from langchain_learning.session_state import SessionState
from src.logger import get_logger

_log = get_logger(__name__)


def _extract_json(tool_result: dict) -> dict:
    """Pull JSON from MCP tool_result content wrapper or raw dict."""
    if "content" in tool_result and isinstance(tool_result.get("content"), list):
        try:
            text = tool_result["content"][0].get("text", "")
            return json.loads(text)
        except Exception:
            pass
    if isinstance(tool_result, dict) and "run_id" in tool_result:
        return tool_result
    try:
        return json.loads(str(tool_result))
    except Exception:
        return {}


class SplunkPostToolNode:
    """PostToolUse bridge for splunk__ MCP tools.

    For splunk__submit_report with status=continue: builds an
    additionalSystemPrompt block containing the next findings so Claude
    automatically sees them on the next turn and continues reasoning.

    Tags: splunk, post-tool-use, findings, additionalSystemPrompt
    """

    def __call__(self, state: SessionState) -> dict:
        entry("splunk_post_tool", state)

        tool_name  = state.get("tool_name", "")
        tool_result = state.get("tool_result") or {}
        session_id = str(state.get("session_id", ""))[:8]

        result = _extract_json(tool_result)

        if tool_name == "splunk__investigate_start":
            run_id = result.get("run_id", "")
            event_count = result.get("event_count", 0)
            _log.info("[splunk_post_tool] investigate_start session=%s run_id=%s events=%d",
                      session_id, run_id[:8] if run_id else "?", event_count)
            # Nothing to inject — Claude already has findings in tool response
            return {}

        if tool_name == "splunk__submit_report":
            status = result.get("status", "")
            run_id = result.get("run_id", "")
            iteration = result.get("iteration", "?")

            if status == "done":
                ui_url = result.get("ui_url", "")
                _log.info("[splunk_post_tool] submit_report done session=%s run_id=%s ui=%s",
                          session_id, run_id[:8] if run_id else "?", ui_url)
                prompt = (
                    f"## Splunk Investigation Complete\n"
                    f"run_id: `{run_id}`  \n"
                    f"Confidence: **{result.get('confidence', '—')}**  \n"
                    f"Iterations: {result.get('iterations', '?')}  \n"
                    f"View full report: {ui_url}\n"
                )
                return {"pending_hook_output": {"additionalSystemPrompt": prompt}}

            if status == "continue":
                findings = result.get("findings", {})
                event_count = result.get("event_count", findings.get("event_count", 0))
                confidence = result.get("confidence", "—")
                _log.info(
                    "[splunk_post_tool] submit_report continue session=%s run_id=%s iter=%s events=%d conf=%s",
                    session_id, run_id[:8] if run_id else "?", iteration, event_count, confidence,
                )
                findings_str = json.dumps(findings, indent=2, default=str)
                prompt = (
                    f"## Splunk Investigation — Iteration {iteration} complete\n"
                    f"run_id: `{run_id}` · Confidence so far: **{confidence}** · Events: {event_count}\n\n"
                    f"New findings from follow-up queries:\n\n"
                    f"```json\n{findings_str}\n```\n\n"
                    f"Reason over these findings and call `splunk__submit_report` again with your updated report and next follow-up queries."
                )
                return {"pending_hook_output": {"additionalSystemPrompt": prompt}}

        return {}
