"""Tests for the resolution gate in handle_finish."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.tools.tasks import handle_create, handle_finish


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    db = tmp_path / "proj_tasks.db"
    with patch("src.tools.tasks._DB", db):
        yield db


def _make_task(resolution: str) -> str:
    body = f"Type: task\nTask: do it\nMotivation: m\nResolution:\n{resolution}\n\nFiles: none"
    r = handle_create(title="Test task", body=body)
    return r["id"]


class TestFinishResolutionGate:
    def test_tbd_blocked(self):
        tid = _make_task("TBD")
        r = handle_finish(task_id=tid, session_id="sess1")
        assert "error" in r
        assert "Resolution" in r["error"]

    def test_tbd_lowercase_blocked(self):
        tid = _make_task("tbd")
        r = handle_finish(task_id=tid, session_id="sess1")
        assert "error" in r

    def test_placeholder_blocked(self):
        tid = _make_task("<to be filled on completion>")
        r = handle_finish(task_id=tid, session_id="sess1")
        assert "error" in r

    def test_empty_resolution_blocked(self):
        tid = _make_task("")
        r = handle_finish(task_id=tid, session_id="sess1")
        assert "error" in r

    def test_filled_resolution_allowed(self):
        tid = _make_task("Implemented the feature and all tests pass.")
        r = handle_finish(task_id=tid, session_id="sess1")
        assert r.get("ok") is True
        assert r["status"] == "done"

    def test_no_resolution_section_allowed(self):
        """Tasks without any Resolution field are not blocked."""
        r = handle_create(title="No res task", body="Just a plain body.")
        tid = r["id"]
        result = handle_finish(task_id=tid, session_id="sess1")
        assert result.get("ok") is True

    def test_hash_heading_style_tbd_blocked(self):
        body = "## Resolution\nTBD\n\n## Notes\nfoo"
        r = handle_create(title="Heading style", body=body)
        tid = r["id"]
        result = handle_finish(task_id=tid, session_id="sess1")
        assert "error" in result

    def test_hash_heading_style_filled_allowed(self):
        body = "## Resolution\nDone — merged PR #42.\n\n## Notes\nfoo"
        r = handle_create(title="Heading filled", body=body)
        tid = r["id"]
        result = handle_finish(task_id=tid, session_id="sess1")
        assert result.get("ok") is True
