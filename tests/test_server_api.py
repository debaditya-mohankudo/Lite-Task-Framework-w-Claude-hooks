"""API-level integration tests for the FastAPI hook server.

Uses FastAPI TestClient — runs the full ASGI stack in-process (lifespan fires,
MemorySaver graph is built). No real server or port binding needed.

These tests are NOT part of the default CI run — they exercise the HTTP wire
layer and are meant to be run manually as a smoke test. Unit tests cover
behavioral correctness; these cover route dispatch, request parsing, and
response shape.

Run with:
    uv run python -m pytest tests/test_server_api.py -v
"""
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).parent.parent
for _p in (str(_PROJECT_ROOT), str(_PROJECT_ROOT / "hooks")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    """Single TestClient for the module — lifespan fires once, graph is built."""
    import langchain_learning.session_graph as sg_mod
    from fastapi.testclient import TestClient
    from hooks.server import app

    sg_mod._graph = None
    with TestClient(app) as c:
        yield c
    sg_mod._graph = None


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_returns_200(self, client):
        r = client.get("/health")
        assert r.status_code == 200

    def test_status_is_ok(self, client):
        r = client.get("/health")
        assert r.json()["status"] == "ok"

    def test_has_sessions_key(self, client):
        r = client.get("/health")
        assert "sessions" in r.json()

    def test_sessions_is_int(self, client):
        r = client.get("/health")
        assert isinstance(r.json()["sessions"], int)


# ---------------------------------------------------------------------------
# /session
# ---------------------------------------------------------------------------

class TestSession:
    def test_returns_200(self, client):
        r = client.get("/session")
        assert r.status_code == 200

    def test_has_count_and_sessions_keys(self, client):
        r = client.get("/session")
        body = r.json()
        assert "count" in body
        assert "sessions" in body

    def test_sessions_is_list(self, client):
        r = client.get("/session")
        assert isinstance(r.json()["sessions"], list)

    def test_count_matches_sessions_length(self, client):
        r = client.get("/session")
        body = r.json()
        assert body["count"] == len(body["sessions"])


# ---------------------------------------------------------------------------
# POST /hook/UserPromptSubmit
# ---------------------------------------------------------------------------

_UPS_PAYLOAD = {
    "session_id": "api-test-ups",
    "cwd": "/tmp",
    "prompt": "what is the capital of France?",
}


class TestUserPromptSubmit:
    def test_returns_200(self, client):
        r = client.post("/hook/UserPromptSubmit", json=_UPS_PAYLOAD)
        assert r.status_code == 200

    def test_response_is_json(self, client):
        r = client.post("/hook/UserPromptSubmit", json=_UPS_PAYLOAD)
        assert r.headers["content-type"].startswith("application/json")

    def test_valid_prompt_returns_dict(self, client):
        r = client.post("/hook/UserPromptSubmit", json=_UPS_PAYLOAD)
        assert isinstance(r.json(), dict)

    def test_hookSpecificOutput_shape_when_present(self, client):
        r = client.post("/hook/UserPromptSubmit", json=_UPS_PAYLOAD)
        body = r.json()
        if body:
            assert "hookSpecificOutput" in body
            assert "additionalSystemPrompt" in body["hookSpecificOutput"]
            assert isinstance(body["hookSpecificOutput"]["additionalSystemPrompt"], str)

    def test_empty_prompt_returns_200(self, client):
        r = client.post("/hook/UserPromptSubmit", json={
            "session_id": "api-test-empty",
            "cwd": "/tmp",
            "prompt": "",
        })
        assert r.status_code == 200

    def test_empty_prompt_returns_empty_body(self, client):
        r = client.post("/hook/UserPromptSubmit", json={
            "session_id": "api-test-empty",
            "cwd": "/tmp",
            "prompt": "",
        })
        assert r.json() == {}

    def test_missing_session_id_does_not_raise(self, client):
        r = client.post("/hook/UserPromptSubmit", json={
            "cwd": "/tmp",
            "prompt": "hello",
        })
        assert r.status_code == 200

    def test_prompt_via_message_content(self, client):
        """Prompt can be nested inside message.content list blocks."""
        r = client.post("/hook/UserPromptSubmit", json={
            "session_id": "api-test-msg",
            "cwd": "/tmp",
            "message": {"content": [{"type": "text", "text": "explain recursion"}]},
        })
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# POST /hook/PreToolUse
# ---------------------------------------------------------------------------

_PTU_SESSION = "api-test-ptu"


@pytest.fixture(scope="module")
def ptu_session(client):
    """Seed a UPS checkpoint so gate has session state to read from."""
    client.post("/hook/UserPromptSubmit", json={
        "session_id": _PTU_SESSION,
        "cwd": "/tmp",
        "prompt": "send a message to alice",
    })


class TestPreToolUse:
    def test_ungated_tool_returns_200(self, client, ptu_session):
        r = client.post("/hook/PreToolUse", json={
            "session_id": _PTU_SESSION,
            "tool_name": "mcp__local-mac__notes__list",
            "tool_input": {},
        })
        assert r.status_code == 200

    def test_ungated_tool_returns_empty(self, client, ptu_session):
        r = client.post("/hook/PreToolUse", json={
            "session_id": _PTU_SESSION,
            "tool_name": "mcp__local-mac__notes__list",
            "tool_input": {},
        })
        assert r.json() == {}

    def test_missing_session_id_fails_open(self, client):
        r = client.post("/hook/PreToolUse", json={
            "tool_name": "mcp__local-mac__imessage__send",
            "tool_input": {"to": "alice", "message": "hi"},
        })
        assert r.status_code == 200
        assert r.json() == {}

    def test_memory_tool_skipped(self, client, ptu_session):
        r = client.post("/hook/PreToolUse", json={
            "session_id": _PTU_SESSION,
            "tool_name": "mcp__local-mac__memory__add",
            "tool_input": {},
        })
        assert r.status_code == 200
        assert r.json() == {}

    def test_gated_tool_denied_without_prereq(self, client):
        """imessage__send denied when no contacts__search prereq in session."""
        r = client.post("/hook/PreToolUse", json={
            "session_id": "api-test-ptu-no-prereq",
            "tool_name": "mcp__local-mac__imessage__send",
            "tool_input": {"to": "alice", "message": "hi"},
        })
        assert r.status_code == 200
        body = r.json()
        assert body.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"

    def test_deny_response_has_reason(self, client):
        r = client.post("/hook/PreToolUse", json={
            "session_id": "api-test-ptu-no-prereq-2",
            "tool_name": "mcp__local-mac__imessage__send",
            "tool_input": {"to": "alice", "message": "hi"},
        })
        body = r.json()
        reason = body.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        assert isinstance(reason, str) and len(reason) > 0

    def test_deny_response_has_hook_event_name(self, client):
        r = client.post("/hook/PreToolUse", json={
            "session_id": "api-test-ptu-no-prereq-3",
            "tool_name": "mcp__local-mac__imessage__send",
            "tool_input": {"to": "alice", "message": "hi"},
        })
        body = r.json()
        assert body.get("hookSpecificOutput", {}).get("hookEventName") == "PreToolUse"

    def test_non_mcp_non_bash_tool_passthrough(self, client, ptu_session):
        """Non-MCP, non-Bash tools are not gated — fail open."""
        r = client.post("/hook/PreToolUse", json={
            "session_id": _PTU_SESSION,
            "tool_name": "Read",
            "tool_input": {"file_path": "/tmp/x"},
        })
        assert r.status_code == 200
        assert r.json() == {}


# ---------------------------------------------------------------------------
# POST /hook/PostToolUse
# ---------------------------------------------------------------------------

class TestPostToolUse:
    def test_returns_200(self, client):
        r = client.post("/hook/PostToolUse", json={
            "session_id": "api-test-ptu2",
            "tool_name": "mcp__local-mac__contacts__search",
            "tool_input": {"query": "alice"},
            "tool_response": {"results": []},
            "duration_ms": 42,
        })
        assert r.status_code == 200

    def test_returns_empty_body(self, client):
        """PTU handler never returns blocking output — always empty."""
        r = client.post("/hook/PostToolUse", json={
            "session_id": "api-test-ptu2",
            "tool_name": "mcp__local-mac__contacts__search",
            "tool_input": {"query": "alice"},
            "tool_response": {"results": []},
            "duration_ms": 42,
        })
        assert r.json() == {}

    def test_non_mcp_tool_skipped(self, client):
        r = client.post("/hook/PostToolUse", json={
            "session_id": "api-test-ptu2",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "tool_response": {},
            "duration_ms": 10,
        })
        assert r.status_code == 200
        assert r.json() == {}

    def test_memory_tool_skipped(self, client):
        r = client.post("/hook/PostToolUse", json={
            "session_id": "api-test-ptu2",
            "tool_name": "mcp__local-mac__memory__add",
            "tool_input": {},
            "tool_response": {},
            "duration_ms": 5,
        })
        assert r.status_code == 200
        assert r.json() == {}

    def test_missing_session_id_returns_200(self, client):
        r = client.post("/hook/PostToolUse", json={
            "tool_name": "mcp__local-mac__contacts__search",
            "tool_input": {},
            "tool_response": {},
        })
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# POST /hook/Stop
# ---------------------------------------------------------------------------

class TestStop:
    def test_returns_200(self, client):
        r = client.post("/hook/Stop", json={"session_id": "api-test-stop"})
        assert r.status_code == 200

    def test_returns_empty_body(self, client):
        r = client.post("/hook/Stop", json={"session_id": "api-test-stop"})
        assert r.json() == {}

    def test_missing_session_id_returns_200(self, client):
        r = client.post("/hook/Stop", json={})
        assert r.status_code == 200
