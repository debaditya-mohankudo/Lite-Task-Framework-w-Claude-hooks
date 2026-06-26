"""Tests for hooks/client.py — the thin HTTP wrapper Claude Code uses to call the hook server.

Runs client.py as a subprocess (matching real usage). Tests cover:
- Payload is enriched with CLAUDE_CWD from env
- Server response is printed to stdout
- Fails open (exit 0, prints {}) when server is unreachable
- Fails open (exit 0, prints {}) on malformed stdin

Not marked integration — no live server needed (uses a local HTTP server fixture).
"""
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_CLIENT = str(Path(__file__).parent.parent / "hooks" / "client.py")
_PYTHON = sys.executable


def _run(event: str, stdin: dict, env: dict | None = None, server: str | None = None) -> subprocess.CompletedProcess:
    merged_env = {**os.environ, **(env or {})}
    if server:
        merged_env["CLAUDE_HOOKS_SERVER"] = server
    return subprocess.run(
        [_PYTHON, _CLIENT, event],
        input=json.dumps(stdin).encode(),
        capture_output=True,
        env=merged_env,
    )


class _CapturingHandler(BaseHTTPRequestHandler):
    """Records last request body; responds with a fixed JSON payload."""

    last_body: dict = {}
    response_body = b'{"hookSpecificOutput": {}}'

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        _CapturingHandler.last_body = json.loads(self.rfile.read(length))
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.response_body)

    def log_message(self, *_):
        pass  # silence request logs in test output


@pytest.fixture(scope="module")
def local_server():
    """Spin up a local HTTP server on a free port; yield its base URL."""
    srv = HTTPServer(("127.0.0.1", 0), _CapturingHandler)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestPayloadEnrichment:
    def test_cwd_injected_from_env(self, local_server):
        _run("UserPromptSubmit", {"session_id": "s1"}, env={"CLAUDE_CWD": "/my/project"}, server=local_server)
        assert _CapturingHandler.last_body.get("cwd") == "/my/project"

    def test_cwd_empty_when_env_unset(self, local_server):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CWD"}
        _run("UserPromptSubmit", {"session_id": "s2"}, env={**env, "CLAUDE_CWD": ""}, server=local_server)
        assert _CapturingHandler.last_body.get("cwd") == ""

    def test_original_fields_preserved(self, local_server):
        _run("PreToolUse", {"session_id": "s3", "tool_name": "Bash"}, server=local_server)
        assert _CapturingHandler.last_body.get("tool_name") == "Bash"
        assert _CapturingHandler.last_body.get("session_id") == "s3"


class TestServerResponse:
    def test_server_response_printed_to_stdout(self, local_server):
        result = _run("PostToolUse", {"session_id": "s4"}, server=local_server)
        assert result.returncode == 0
        out = json.loads(result.stdout)
        assert "hookSpecificOutput" in out

    def test_exit_zero_on_success(self, local_server):
        result = _run("Stop", {"session_id": "s5"}, server=local_server)
        assert result.returncode == 0


class TestFailOpen:
    def test_exits_zero_when_server_unreachable(self):
        # Point at a port nothing is listening on
        env = {**os.environ, "CLAUDE_HOOKS_SERVER": "http://127.0.0.1:19999"}
        result = subprocess.run(
            [_PYTHON, _CLIENT, "UserPromptSubmit"],
            input=b'{"session_id":"s6"}',
            capture_output=True,
            env=env,
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {}

    def test_prints_empty_json_when_unreachable(self):
        env = {**os.environ, "CLAUDE_HOOKS_SERVER": "http://127.0.0.1:19999"}
        result = subprocess.run(
            [_PYTHON, _CLIENT, "Stop"],
            input=b'{"session_id":"s7"}',
            capture_output=True,
            env=env,
        )
        assert result.stdout.strip() == b"{}"

    def test_stderr_message_on_failure(self):
        env = {**os.environ, "CLAUDE_HOOKS_SERVER": "http://127.0.0.1:19999"}
        result = subprocess.run(
            [_PYTHON, _CLIENT, "Stop"],
            input=b'{"session_id":"s8"}',
            capture_output=True,
            env=env,
        )
        assert b"failing open" in result.stderr

    def test_exits_zero_on_missing_event_arg(self):
        result = subprocess.run(
            [_PYTHON, _CLIENT],
            input=b"{}",
            capture_output=True,
        )
        assert result.returncode == 1  # usage error, not fail-open
