"""Tests for issue_type column on open_tasks — create, update, validation."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from src.tools.tasks import handle_create, handle_get, handle_list, handle_update


@pytest.fixture(autouse=True)
def isolated_db(tmp_path):
    db = tmp_path / "proj_tasks.db"
    with patch("src.tools.tasks._DB", db):
        yield db


class TestCreateIssueType:
    def test_default_is_task(self):
        r = handle_create(title="My task", body="Type: feature\nTask: x\nResolution: y\nMotivation: z\nFiles: f")
        assert r["issue_type"] == "task"

    def test_explicit_epic(self):
        r = handle_create(title="Big epic", body="Type: feature\nTask: x\nResolution: y\nMotivation: z\nFiles: f", issue_type="epic")
        assert r["issue_type"] == "epic"

    def test_all_valid_types(self):
        for itype in ("epic", "story", "task", "bug", "subtask"):
            r = handle_create(title=f"t-{itype}", body="Type: feature\nTask: x\nResolution: y\nMotivation: z\nFiles: f", issue_type=itype)
            assert r["issue_type"] == itype

    def test_invalid_type_returns_error(self):
        r = handle_create(title="bad", body="Type: feature\nTask: x\nResolution: y\nMotivation: z\nFiles: f", issue_type="sprint")
        assert "error" in r


class TestUpdateIssueType:
    def _create(self, issue_type="task"):
        return handle_create(
            title="base task",
            body="Type: feature\nTask: x\nResolution: y\nMotivation: z\nFiles: f",
            issue_type=issue_type,
        )["id"]

    def test_update_issue_type(self):
        tid = self._create()
        r = handle_update(id=tid, issue_type="bug")
        assert r["issue_type"] == "bug"

    def test_update_preserves_issue_type_when_not_specified(self):
        tid = self._create(issue_type="story")
        r = handle_update(id=tid, title="new title")
        assert r["issue_type"] == "story"

    def test_update_invalid_type_returns_error(self):
        tid = self._create()
        r = handle_update(id=tid, issue_type="invalid")
        assert "error" in r


class TestGetAndListIssueType:
    def test_get_returns_issue_type(self):
        tid = handle_create(
            title="story task",
            body="Type: feature\nTask: x\nResolution: y\nMotivation: z\nFiles: f",
            issue_type="story",
        )["id"]
        r = handle_get(id=tid)
        assert r["issue_type"] == "story"

    def test_list_returns_issue_type(self):
        handle_create(
            title="listed epic",
            body="Type: feature\nTask: x\nResolution: y\nMotivation: z\nFiles: f",
            issue_type="epic",
        )
        rows = handle_list()
        assert any(t["issue_type"] == "epic" for t in rows)
