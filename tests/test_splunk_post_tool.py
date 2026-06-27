"""Tests for SplunkPostToolNode — PostToolUse bridge for splunk__ MCP tools."""
from __future__ import annotations

import json

import pytest

from langchain_learning.nodes.splunk_post_tool import SplunkPostToolNode, _extract_json


# ── _extract_json helpers ──────────────────────────────────────────────────────

def test_extract_json_from_mcp_content_wrapper():
    payload = {"run_id": "abc", "status": "done"}
    tool_result = {"content": [{"text": json.dumps(payload)}]}
    assert _extract_json(tool_result) == payload


def test_extract_json_from_raw_dict_with_run_id():
    payload = {"run_id": "abc", "status": "continue"}
    assert _extract_json(payload) == payload


def test_extract_json_from_json_string():
    payload = {"run_id": "abc", "status": "done"}
    assert _extract_json(json.dumps(payload)) == payload


def test_extract_json_returns_empty_on_garbage():
    assert _extract_json({"no_run_id": True}) == {}
    assert _extract_json("not json!!") == {}


# ── state builder ──────────────────────────────────────────────────────────────

def _state(tool_name: str, tool_result: dict) -> dict:
    return {
        "session_id": "sess-test-1234",
        "tool_name": tool_name,
        "tool_result": tool_result,
    }


def _wrap(payload: dict) -> dict:
    return {"content": [{"text": json.dumps(payload)}]}


# ── splunk__investigate_start ──────────────────────────────────────────────────

def test_investigate_start_returns_empty():
    node = SplunkPostToolNode()
    result = node(_state(
        "splunk__investigate_start",
        _wrap({"run_id": "run-001", "event_count": 120, "findings": {}}),
    ))
    assert result == {}


def test_investigate_start_with_missing_run_id():
    node = SplunkPostToolNode()
    result = node(_state("splunk__investigate_start", _wrap({"event_count": 50})))
    assert result == {}


# ── splunk__submit_report: status=done ────────────────────────────────────────

def test_submit_report_done_injects_completion_prompt():
    node = SplunkPostToolNode()
    result = node(_state(
        "splunk__submit_report",
        _wrap({
            "status": "done",
            "run_id": "run-001",
            "confidence": "High",
            "iterations": 2,
            "ui_url": "http://127.0.0.1:8765/ui/runs/run-001",
        }),
    ))
    assert "pending_hook_output" in result
    prompt = result["pending_hook_output"]["additionalSystemPrompt"]
    assert "Splunk Investigation Complete" in prompt
    assert "run-001" in prompt
    assert "High" in prompt
    assert "http://127.0.0.1:8765/ui/runs/run-001" in prompt


def test_submit_report_done_without_ui_url():
    node = SplunkPostToolNode()
    result = node(_state(
        "splunk__submit_report",
        _wrap({"status": "done", "run_id": "run-002", "confidence": "Medium", "iterations": 1}),
    ))
    prompt = result["pending_hook_output"]["additionalSystemPrompt"]
    assert "Complete" in prompt


# ── splunk__submit_report: status=continue ────────────────────────────────────

def test_submit_report_continue_injects_findings_prompt():
    findings = {"event_count": 320, "spikes": [], "patterns": []}
    node = SplunkPostToolNode()
    result = node(_state(
        "splunk__submit_report",
        _wrap({
            "status": "continue",
            "run_id": "run-003",
            "iteration": 1,
            "confidence": "Medium",
            "event_count": 320,
            "findings": findings,
        }),
    ))
    assert "pending_hook_output" in result
    prompt = result["pending_hook_output"]["additionalSystemPrompt"]
    assert "Iteration 1" in prompt
    assert "run-003" in prompt
    assert "Medium" in prompt
    assert "splunk__submit_report" in prompt
    assert json.dumps(findings, indent=2) in prompt


def test_submit_report_continue_event_count_from_findings_fallback():
    findings = {"event_count": 50}
    node = SplunkPostToolNode()
    result = node(_state(
        "splunk__submit_report",
        _wrap({
            "status": "continue",
            "run_id": "run-004",
            "iteration": 2,
            "confidence": "Low",
            "findings": findings,
            # no top-level event_count
        }),
    ))
    prompt = result["pending_hook_output"]["additionalSystemPrompt"]
    assert "Events: 50" in prompt


# ── unknown tool / unknown status ─────────────────────────────────────────────

def test_unknown_tool_returns_empty():
    node = SplunkPostToolNode()
    result = node(_state("some_other_tool", _wrap({"status": "done"})))
    assert result == {}


def test_submit_report_unknown_status_returns_empty():
    node = SplunkPostToolNode()
    result = node(_state(
        "splunk__submit_report",
        _wrap({"status": "pending", "run_id": "run-005"}),
    ))
    assert result == {}


def test_empty_tool_result_returns_empty():
    node = SplunkPostToolNode()
    result = node(_state("splunk__submit_report", {}))
    assert result == {}
