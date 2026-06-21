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


# ---------------------------------------------------------------------------
# LoadActiveReviewNode — unit tests
# ---------------------------------------------------------------------------

class TestLoadActiveReviewNode:
    """Unit-test the node's load/overlay/skip behaviour."""

    def _node(self):
        from langchain_learning.nodes.load_active_review import LoadActiveReviewNode
        return LoadActiveReviewNode()

    def _run(self, tasks_db, templates_dir, state):
        mock_cfg = MagicMock()
        mock_cfg.tasks_db = tasks_db
        with patch("langchain_learning.nodes.load_active_review._cfg", mock_cfg), \
             patch("langchain_learning.nodes.load_active_review._REVIEW_TEMPLATES_DIR", templates_dir):
            return self._node()(state)

    def test_no_active_task_returns_empty(self, tasks_db, tmp_path):
        result = self._run(tasks_db, tmp_path, {"active_task_id": "", "session_id": "s"})
        assert result == {"active_review": {}}

    def test_no_review_child_returns_empty(self, tasks_db, tmp_path):
        # Work task exists but has no review child
        work_id = _make_work_task(tags="")
        _write_template(tmp_path)
        result = self._run(tasks_db, tmp_path, {"active_task_id": work_id, "session_id": "s"})
        assert result == {"active_review": {}}

    def test_open_review_child_returns_items(self, tasks_db, tmp_path):
        work_id = _make_work_task()
        review_id = tasks.handle_set_active(work_id, "s")["review_task_id"]
        _write_template(tmp_path)
        result = self._run(tasks_db, tmp_path, {"active_task_id": work_id, "session_id": "s"})
        rev = result["active_review"]
        assert rev["review_task_id"] == review_id
        assert rev["template"] == "correctness"
        ids = {i["id"] for i in rev["items"]}
        assert {"c1", "c2", "m1"} <= ids
        # No verdicts yet → all pending
        assert all(i["status"] == "pending" for i in rev["items"])

    def test_review_result_overlays_persisted_status(self, tasks_db, tmp_path):
        work_id = _make_work_task()
        review_id = tasks.handle_set_active(work_id, "s")["review_task_id"]
        tasks.handle_execute_review(review_id, [
            {"id": "c1", "passed": True, "note": "ok"},
            {"id": "c2", "passed": False, "note": "bad"},
        ])
        _write_template(tmp_path)
        result = self._run(tasks_db, tmp_path, {"active_task_id": work_id, "session_id": "s"})
        items = {i["id"]: i for i in result["active_review"]["items"]}
        assert items["c1"]["status"] == "pass"
        assert items["c1"]["note"] == "ok"
        assert items["c2"]["status"] == "fail"
        assert items["m1"]["status"] == "pending"  # untouched manual item

    def test_missing_template_file_returns_empty_items(self, tasks_db, tmp_path):
        work_id = _make_work_task()
        tasks.handle_set_active(work_id, "s")
        # No template written to tmp_path
        result = self._run(tasks_db, tmp_path, {"active_task_id": work_id, "session_id": "s"})
        assert result["active_review"]["items"] == []

    def test_db_error_returns_empty(self, tasks_db, tmp_path):
        _make_work_task()  # ensure the DB file exists so .exists() check passes
        _write_template(tmp_path)
        mock_cfg = MagicMock()
        mock_cfg.tasks_db = tasks_db
        with patch("langchain_learning.nodes.load_active_review._cfg", mock_cfg), \
             patch("langchain_learning.nodes.load_active_review._REVIEW_TEMPLATES_DIR", tmp_path), \
             patch("langchain_learning.nodes.load_active_review.sqlite3.connect",
                   side_effect=RuntimeError("db down")):
            result = self._node()({"active_task_id": "whatever", "session_id": "s"})
        assert result == {"active_review": {}}


# ---------------------------------------------------------------------------
# Review MCP handlers
# ---------------------------------------------------------------------------

class TestSetActiveReviewBranch:
    """handle_set_active auto-creates a review child from a review:<template> tag."""

    def test_review_tag_creates_child(self, tasks_db):
        work_id = _make_work_task(tags="review:correctness")
        res = tasks.handle_set_active(work_id, "s")
        review_id = res["review_task_id"]
        with tasks._connect() as conn:
            row = conn.execute(
                "SELECT parent_id, issue_type, review_template_name, tags FROM open_tasks WHERE id=?",
                (review_id,),
            ).fetchone()
        assert row["parent_id"] == work_id
        assert row["issue_type"] == "review"
        assert row["review_template_name"] == "correctness"
        assert "review:correctness" in row["tags"]

    def test_idempotent_no_duplicate_child(self, tasks_db):
        work_id = _make_work_task(tags="review:correctness")
        first = tasks.handle_set_active(work_id, "s")["review_task_id"]
        second = tasks.handle_set_active(work_id, "s")["review_task_id"]
        assert first == second
        with tasks._connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM open_tasks WHERE parent_id=? AND issue_type='review'",
                (work_id,),
            ).fetchone()[0]
        assert n == 1

    def test_no_review_tag_no_child(self, tasks_db):
        work_id = _make_work_task(tags="")
        res = tasks.handle_set_active(work_id, "s")
        assert "review_task_id" not in res
        with tasks._connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM open_tasks WHERE issue_type='review'"
            ).fetchone()[0]
        assert n == 0


class TestExecuteReview:
    """handle_execute_review merges verdicts and computes status."""

    def _review(self, tasks_db) -> str:
        work_id = _make_work_task(tags="review:correctness")
        return tasks.handle_set_active(work_id, "s")["review_task_id"]

    def test_all_pass_is_done(self, tasks_db):
        rid = self._review(tasks_db)
        res = tasks.handle_execute_review(rid, [
            {"id": "c1", "passed": True, "note": ""},
            {"id": "c2", "passed": True, "note": ""},
        ])
        assert res["status"] == "done"
        assert res["passed"] == 2 and res["failed"] == 0

    def test_any_fail_is_blocked(self, tasks_db):
        rid = self._review(tasks_db)
        res = tasks.handle_execute_review(rid, [
            {"id": "c1", "passed": True, "note": ""},
            {"id": "c2", "passed": False, "note": "broken"},
        ])
        assert res["status"] == "blocked"
        assert res["failed"] == 1

    def test_pending_is_open(self, tasks_db):
        rid = self._review(tasks_db)
        res = tasks.handle_execute_review(rid, [
            {"id": "c1", "passed": None, "note": ""},
        ])
        assert res["status"] == "open"
        assert res["pending"] == 1

    def test_merge_preserves_prior_items(self, tasks_db):
        rid = self._review(tasks_db)
        tasks.handle_execute_review(rid, [{"id": "c1", "passed": True, "note": "first"}])
        res = tasks.handle_execute_review(rid, [{"id": "c2", "passed": True, "note": "second"}])
        # c1 from the first call must still be counted
        assert res["passed"] == 2

    def test_unknown_review_task_errors(self, tasks_db):
        res = tasks.handle_execute_review("nope", [{"id": "c1", "passed": True}])
        assert "error" in res


class TestSubmitReviewItem:
    """handle_submit_review_item signs off a manual item and recomputes status."""

    def _review(self, tasks_db) -> str:
        work_id = _make_work_task(tags="review:correctness")
        return tasks.handle_set_active(work_id, "s")["review_task_id"]

    def test_manual_signoff_updates_item(self, tasks_db):
        rid = self._review(tasks_db)
        tasks.handle_execute_review(rid, [{"id": "c1", "passed": True}])
        res = tasks.handle_submit_review_item(rid, "m1", True, note="verified")
        # status should now reflect both auto + manual passing
        assert res.get("status") in ("done", "open", "blocked")
        with tasks._connect() as conn:
            rr = conn.execute("SELECT review_result FROM open_tasks WHERE id=?", (rid,)).fetchone()[0]
        assert "m1" in rr and "verified" in rr

    def test_manual_reject_blocks(self, tasks_db):
        rid = self._review(tasks_db)
        res = tasks.handle_submit_review_item(rid, "m1", False, note="missing test")
        assert res["status"] == "blocked"


class TestReviewTemplates:
    """create/list review template round-trip."""

    @pytest.fixture
    def templates_dir(self, tmp_path, monkeypatch):
        d = tmp_path / "review_templates"
        monkeypatch.setattr(tasks, "_REVIEW_TEMPLATES_DIR", d)
        return d

    def test_create_then_list(self, templates_dir):
        res = tasks.handle_create_review_template(
            name="security",
            domain="claude-hooks",
            context_prompt="Check for security issues.",
            checklist=["[auto] s1: no secrets logged", "[manual] s2: pen-tested"],
        )
        assert res["ok"] is True
        assert (templates_dir / "security.md").exists()

        listed = tasks.handle_list_review_templates()
        by_name = {t["name"]: t for t in listed}
        assert "security" in by_name
        assert by_name["security"]["domain"] == "claude-hooks"
        assert by_name["security"]["item_count"] == 2

    def test_list_empty_when_dir_absent(self, templates_dir):
        # dir not created yet
        assert tasks.handle_list_review_templates() == []
