"""Integration tests for the review state lifecycle (epic: 17b841d9).

Hits the production hook server on :8766 — requires the server to be running.
Tests are skipped automatically when the server is not reachable.

Run:
    uv run python -m pytest tests/test_review_lifecycle_integration.py -v

Covers:
  - TaskDoneGate: done blocked/allowed based on task state and review runs
  - Manual approval bypass (non-empty reason required)
  - Review-tag guard: review:<template> tags only allowed in review state
  - Auto-review transition: UPS "task:<id> done" signal moves task to review

All test tasks are tagged test:integration and cleaned up after each test.
All session IDs use a per-test unique suffix so each PreToolUse call gets a
fresh LangGraph thread (stale SqliteSaver checkpoint otherwise returns the
saved state without re-running the graph).
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
import requests

_BASE = "http://127.0.0.1:8766"


# ---------------------------------------------------------------------------
# Server availability check — skip all tests when server is down
# ---------------------------------------------------------------------------

def _server_up() -> bool:
    try:
        return requests.get(f"{_BASE}/health", timeout=2).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.integration


@pytest.fixture(scope="session", autouse=True)
def require_server():
    if not _server_up():
        pytest.skip("Hook server not reachable at :8766 — start it before running integration tests")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(status: str = "open", tags: str = "", cleanup: list | None = None) -> str:
    """Insert a task directly into the prod DB tagged as test data. Returns task id."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import src.tools.tasks as tasks_mod
    tid = "api-itg-" + uuid.uuid4().hex[:8]
    merged_tags = ",".join(filter(None, ["test:integration", tags]))
    with tasks_mod._connect() as conn:
        conn.execute(
            "INSERT INTO open_tasks (id, title, status, tags) VALUES (?, ?, ?, ?)",
            (tid, f"[TEST] Integration task {tid}", status, merged_tags),
        )
    if cleanup is not None:
        cleanup.append(tid)
    return tid


def _add_review_run(task_id: str, template: str = "correctness", status: str = "open") -> str:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import src.tools.tasks as tasks_mod
    run_id = "api-run-" + uuid.uuid4().hex[:8]
    with tasks_mod._connect() as conn:
        conn.execute(
            "INSERT INTO review_runs (id, task_id, template_name, status) VALUES (?, ?, ?, ?)",
            (run_id, task_id, template, status),
        )
    return run_id


def _task_status(task_id: str) -> str:
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import src.tools.tasks as tasks_mod
    with tasks_mod._connect() as conn:
        row = conn.execute("SELECT status FROM open_tasks WHERE id=?", (task_id,)).fetchone()
    return row["status"] if row else ""


@pytest.fixture(autouse=True)
def cleanup_tasks():
    created: list[str] = []
    yield created
    if created:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent))
        import src.tools.tasks as tasks_mod
        with tasks_mod._connect() as conn:
            for tid in created:
                conn.execute("DELETE FROM review_runs WHERE task_id=?", (tid,))
                conn.execute("DELETE FROM open_tasks WHERE id=?", (tid,))


def _ptu(task_id: str, status: str = "done", tags: str = "", body: str = "") -> dict:
    """POST PreToolUse for tasks__update. Uses unique session per call for fresh LangGraph thread."""
    session = "api-test-gate-" + uuid.uuid4().hex[:8]
    tool_input: dict = {"id": task_id}
    if status:
        tool_input["status"] = status
    if tags:
        tool_input["tags"] = tags
    if body:
        tool_input["body"] = body
    r = requests.post(f"{_BASE}/hook/PreToolUse", json={
        "session_id": session,
        "tool_name": "mcp__claude-hooks__tasks__update",
        "tool_input": tool_input,
    }, timeout=10)
    assert r.status_code == 200
    return r.json()


def _is_denied(body: dict) -> bool:
    return body.get("hookSpecificOutput", {}).get("permissionDecision") == "deny"


def _ups(session: str, prompt: str, **extra) -> dict:
    r = requests.post(f"{_BASE}/hook/UserPromptSubmit", json={
        "session_id": session, "cwd": "/tmp", "prompt": prompt, **extra,
    }, timeout=10)
    assert r.status_code == 200
    return r.json()


# ---------------------------------------------------------------------------
# TaskDoneGate — done-transition guard
# ---------------------------------------------------------------------------

class TestTaskDoneGateHTTP:

    def test_done_from_open_is_denied(self, cleanup_tasks):
        tid = _make_task(status="open", cleanup=cleanup_tasks)
        assert _is_denied(_ptu(tid, status="done"))

    def test_done_from_active_is_denied(self, cleanup_tasks):
        tid = _make_task(status="active", cleanup=cleanup_tasks)
        assert _is_denied(_ptu(tid, status="done"))

    def test_deny_reason_mentions_review(self, cleanup_tasks):
        tid = _make_task(status="open", cleanup=cleanup_tasks)
        body = _ptu(tid, status="done")
        reason = body.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        assert "review" in reason.lower()

    def test_deny_reason_contains_task_id(self, cleanup_tasks):
        tid = _make_task(status="open", cleanup=cleanup_tasks)
        body = _ptu(tid, status="done")
        reason = body.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        assert tid in reason

    def test_done_from_review_no_runs_is_allowed(self, cleanup_tasks):
        tid = _make_task(status="review", cleanup=cleanup_tasks)
        assert not _is_denied(_ptu(tid, status="done"))

    def test_done_from_review_with_done_run_is_allowed(self, cleanup_tasks):
        tid = _make_task(status="review", cleanup=cleanup_tasks)
        _add_review_run(tid, status="done")
        assert not _is_denied(_ptu(tid, status="done"))

    def test_done_from_review_with_pending_run_is_denied(self, cleanup_tasks):
        tid = _make_task(status="review", cleanup=cleanup_tasks)
        _add_review_run(tid, status="open")
        assert _is_denied(_ptu(tid, status="done"))

    def test_done_from_review_with_blocked_run_is_denied(self, cleanup_tasks):
        tid = _make_task(status="review", cleanup=cleanup_tasks)
        _add_review_run(tid, status="blocked")
        assert _is_denied(_ptu(tid, status="done"))

    def test_non_done_status_always_passes(self, cleanup_tasks):
        tid = _make_task(status="open", cleanup=cleanup_tasks)
        assert not _is_denied(_ptu(tid, status="review"))

    def test_missing_task_id_fails_open(self):
        session = "api-test-gate-" + uuid.uuid4().hex[:8]
        r = requests.post(f"{_BASE}/hook/PreToolUse", json={
            "session_id": session,
            "tool_name": "mcp__claude-hooks__tasks__update",
            "tool_input": {"status": "done"},
        }, timeout=10)
        assert r.status_code == 200
        assert not _is_denied(r.json())

    def test_unknown_task_fails_open(self):
        assert not _is_denied(_ptu("deadbeef", status="done"))


# ---------------------------------------------------------------------------
# TaskDoneGate — manual approval bypass
# ---------------------------------------------------------------------------

class TestManualApprovalBypass:

    def test_bypass_with_reason_allows_done(self, cleanup_tasks):
        tid = _make_task(status="review", cleanup=cleanup_tasks)
        _add_review_run(tid, status="open")
        assert not _is_denied(_ptu(tid, status="done", body="Manual approval: confirmed via chat"))

    def test_bypass_empty_reason_is_denied(self, cleanup_tasks):
        tid = _make_task(status="review", cleanup=cleanup_tasks)
        _add_review_run(tid, status="open")
        assert _is_denied(_ptu(tid, status="done", body="Manual approval: "))

    def test_bypass_whitespace_reason_is_denied(self, cleanup_tasks):
        tid = _make_task(status="review", cleanup=cleanup_tasks)
        _add_review_run(tid, status="open")
        assert _is_denied(_ptu(tid, status="done", body="Manual approval:   "))

    def test_bypass_case_insensitive(self, cleanup_tasks):
        tid = _make_task(status="review", cleanup=cleanup_tasks)
        _add_review_run(tid, status="open")
        assert not _is_denied(_ptu(tid, status="done", body="MANUAL APPROVAL: skip for hotfix"))

    def test_bypass_does_not_skip_state_machine_guard(self, cleanup_tasks):
        """Manual approval only bypasses the run check — state machine still blocks from open."""
        tid = _make_task(status="open", cleanup=cleanup_tasks)
        assert _is_denied(_ptu(tid, status="done", body="Manual approval: urgent"))


# ---------------------------------------------------------------------------
# Review-tag guard
# ---------------------------------------------------------------------------

class TestReviewTagGuardHTTP:

    def test_review_tag_blocked_when_open(self, cleanup_tasks):
        tid = _make_task(status="open", cleanup=cleanup_tasks)
        assert _is_denied(_ptu(tid, status="", tags="review:correctness"))

    def test_review_tag_blocked_when_active(self, cleanup_tasks):
        tid = _make_task(status="active", cleanup=cleanup_tasks)
        assert _is_denied(_ptu(tid, status="", tags="review:correctness"))

    def test_review_tag_allowed_when_in_review(self, cleanup_tasks):
        tid = _make_task(status="review", cleanup=cleanup_tasks)
        assert not _is_denied(_ptu(tid, status="", tags="review:correctness"))

    def test_non_review_tag_always_passes(self, cleanup_tasks):
        tid = _make_task(status="open", cleanup=cleanup_tasks)
        assert not _is_denied(_ptu(tid, status="", tags="project:claude-hooks"))

    def test_review_tag_deny_reason_mentions_review_state(self, cleanup_tasks):
        tid = _make_task(status="open", cleanup=cleanup_tasks)
        body = _ptu(tid, status="", tags="review:correctness")
        reason = body.get("hookSpecificOutput", {}).get("permissionDecisionReason", "")
        assert "review" in reason.lower()


# ---------------------------------------------------------------------------
# Auto-review transition via UserPromptSubmit
# ---------------------------------------------------------------------------

def _make_hex_task(status: str = "open", cleanup: list | None = None) -> str:
    """Like _make_task but with a pure hex ID so it matches the done-signal regex."""
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import src.tools.tasks as tasks_mod
    tid = uuid.uuid4().hex[:8]  # pure hex — matches \btask:[a-f0-9]{6,}\s+done\b
    with tasks_mod._connect() as conn:
        conn.execute(
            "INSERT INTO open_tasks (id, title, status, tags) VALUES (?, ?, ?, ?)",
            (tid, f"[TEST] Integration task {tid}", status, "test:integration"),
        )
    if cleanup is not None:
        cleanup.append(tid)
    return tid


class TestAutoReviewTransitionHTTP:
    """'task:<id> done' in a UPS prompt auto-transitions the active task to review.

    LogTaskEventsNode reads active_task_id from the LangGraph checkpoint. A prior
    UPS turn that seeds active_task_id in the checkpoint is required before the
    'done' prompt fires — passing active_task_id in the HTTP payload alone is not
    enough because it is not surfaced to the node.

    Note: task IDs must be pure hex to match the done-signal regex
    (_TASK_DONE_PATTERN = r"\\btask:[a-f0-9]{6,}\\s+done\\b") — use _make_hex_task.
    """

    def _seed(self, sid: str, tid: str) -> None:
        """Plant active_task_id in the session checkpoint via tasks__set_active PostToolUse."""
        requests.post(f"{_BASE}/hook/PostToolUse", json={
            "session_id": sid,
            "tool_name": "mcp__claude-hooks__tasks__set_active",
            "tool_input": {"task_id": tid},
            "tool_response": {"ok": True, "task_id": tid, "title": "Test task"},
            "duration_ms": 1,
        }, timeout=10)

    def test_task_done_signal_moves_task_to_review(self, cleanup_tasks):
        tid = _make_hex_task(status="active", cleanup=cleanup_tasks)
        sid = "api-test-ups-" + uuid.uuid4().hex[:8]
        # Seed a UPS turn first (initialises the checkpoint), then activate the task
        _ups(sid, "starting work on the task")
        self._seed(sid, tid)
        # Fire: 'done' signal on the next UPS turn
        _ups(sid, f"task:{tid} done — all acceptance criteria met")
        assert _task_status(tid) == "review"

    def test_non_done_prompt_leaves_task_active(self, cleanup_tasks):
        tid = _make_hex_task(status="active", cleanup=cleanup_tasks)
        sid = "api-test-ups-" + uuid.uuid4().hex[:8]
        _ups(sid, "starting work on the task")
        self._seed(sid, tid)
        _ups(sid, "still working on this task")
        assert _task_status(tid) == "active"
