"""Tests for commit_task_map — mapping commit SHAs back to the task that produced them."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from src.tools.tasks import handle_create, handle_get_commits, record_commit


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    db = tmp_path / "proj_tasks.db"
    with patch("src.tools.tasks._DB", db):
        yield db


def _make_task() -> str:
    r = handle_create(
        title="Some task",
        body="Type: feature\nTask: x\nResolution: y\nMotivation: z\nFiles: f",
    )
    return r["id"]


class TestRecordCommit:
    def test_record_commit_succeeds(self):
        task_id = _make_task()
        result = record_commit(task_id=task_id, commit_hash="abc1234", repo_path="/repo")
        assert result["ok"] is True
        assert result["task_id"] == task_id
        assert result["commit_hash"] == "abc1234"

    def test_missing_task_id_errors(self):
        result = record_commit(task_id="", commit_hash="abc1234")
        assert "error" in result

    def test_missing_commit_hash_errors(self):
        task_id = _make_task()
        result = record_commit(task_id=task_id, commit_hash="")
        assert "error" in result

    def test_unknown_task_id_errors(self):
        result = record_commit(task_id="doesnotexist", commit_hash="abc1234")
        assert "error" in result

    def test_duplicate_commit_is_idempotent(self):
        task_id = _make_task()
        record_commit(task_id=task_id, commit_hash="abc1234", repo_path="/repo")
        record_commit(task_id=task_id, commit_hash="abc1234", repo_path="/repo")
        rows = handle_get_commits(task_id)
        assert len(rows) == 1


class TestGetCommits:
    def test_returns_empty_for_task_with_no_commits(self):
        task_id = _make_task()
        assert handle_get_commits(task_id) == []

    def test_returns_recorded_commits_most_recent_first(self):
        task_id = _make_task()
        record_commit(task_id=task_id, commit_hash="aaa1111", repo_path="/repo")
        record_commit(task_id=task_id, commit_hash="bbb2222", repo_path="/repo")
        rows = handle_get_commits(task_id)
        hashes = [r["commit_hash"] for r in rows]
        assert set(hashes) == {"aaa1111", "bbb2222"}

    def test_does_not_leak_commits_from_other_tasks(self):
        task_a = _make_task()
        task_b = _make_task()
        record_commit(task_id=task_a, commit_hash="aaa1111")
        rows = handle_get_commits(task_b)
        assert rows == []
