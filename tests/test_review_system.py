"""Tests for the Review Template System.

Covers the MCP handlers (src/tools/tasks.py) and the LoadActiveReviewNode
(langchain_learning/nodes/load_active_review.py).

Setup note: handlers resolve their DB via the module-level src.tools.tasks._DB,
and _connect() auto-runs _ensure_db + _migrate — so pointing _DB at a temp file
yields a fresh, correctly-migrated schema with no manual DDL.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import src.tools.tasks as tasks


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def tasks_db(tmp_path, monkeypatch):
    """Point the tasks module at a fresh temp DB."""
    db = tmp_path / "proj_tasks.db"
    monkeypatch.setattr(tasks, "_DB", db)
    return db


def _make_work_task(title="Work task", tags="review:correctness") -> str:
    """Create a work task and stamp a review:<template> tag on it. Returns id."""
    res = tasks.handle_create(title=title)
    tid = res["id"]
    if tags:
        tasks.handle_update(id=tid, tags=tags)
    return tid


def _write_template(dir_path: Path, name="correctness") -> Path:
    dir_path.mkdir(parents=True, exist_ok=True)
    path = dir_path / f"{name}.md"
    path.write_text(
        "---\n"
        f"name: {name}\n"
        "domain: claude-hooks\n"
        "---\n\n"
        "## Auto items\n\n"
        "- [auto] c1: state keys owned per node\n"
        "- [auto] c2: read before write\n\n"
        "## Manual items\n\n"
        "- [manual] m1: tested against real session\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# Regression tests — lock in the 3 fixes shipped under epic:33c4e5fe
# ---------------------------------------------------------------------------

class TestReviewRegressions:
    """Guard the three review-system fixes against silent re-breakage."""

    # ── fix 1: blocked review stays visible ──────────────────────────────

    def test_blocked_review_visible_in_node(self, tasks_db, tmp_path):
        """A review child with status='blocked' is still loaded by the node."""
        work_id = _make_work_task()
        sa = tasks.handle_set_active(work_id, "sess-1")
        review_id = sa["review_task_id"]
        # Record a failure → status becomes 'blocked'
        tasks.handle_execute_review(review_id, [{"id": "c1", "passed": False, "note": "nope"}])

        templates_dir = tmp_path / "review_templates"
        _write_template(templates_dir)

        from langchain_learning.nodes.load_active_review import LoadActiveReviewNode
        mock_cfg = MagicMock()
        mock_cfg.tasks_db = tasks_db
        with patch("langchain_learning.nodes.load_active_review._cfg", mock_cfg), \
             patch("langchain_learning.nodes.load_active_review._REVIEW_TEMPLATES_DIR", templates_dir):
            result = LoadActiveReviewNode()({"active_task_id": work_id, "session_id": "sess-1"})

        assert result["active_review"]["review_task_id"] == review_id
        assert result["active_review"]["items"], "blocked review must still surface its checklist"

    def test_blocked_review_in_list_default(self, tasks_db):
        """handle_list() default (open,blocked) includes a blocked review."""
        work_id = _make_work_task()
        review_id = tasks.handle_set_active(work_id, "sess-1")["review_task_id"]
        tasks.handle_execute_review(review_id, [{"id": "c1", "passed": False, "note": "x"}])

        listed_ids = {t["id"] for t in tasks.handle_list()}
        assert review_id in listed_ids

    # ── fix 2: fail-open set_active ──────────────────────────────────────

    def test_set_active_fail_open_when_review_child_raises(self, tasks_db):
        """A crash in _create_review_child must not fail task activation."""
        work_id = _make_work_task()
        with patch.object(tasks, "_create_review_child", side_effect=RuntimeError("boom")):
            res = tasks.handle_set_active(work_id, "sess-1")
        assert res["ok"] is True
        assert res["task_id"] == work_id
        assert "review_task_id" not in res

    # ── fix 3: reviews column dropped ────────────────────────────────────

    def test_fresh_schema_has_no_reviews_column(self, tasks_db):
        with tasks._connect() as conn:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(open_tasks)")}
        assert "reviews" not in cols
        assert "review_template_name" in cols
        assert "review_result" in cols

    def test_review_child_insert_works_without_reviews_column(self, tasks_db):
        work_id = _make_work_task()
        res = tasks.handle_set_active(work_id, "sess-1")
        assert "review_task_id" in res
