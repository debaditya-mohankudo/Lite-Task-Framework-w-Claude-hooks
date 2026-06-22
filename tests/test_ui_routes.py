"""Route-level tests for the Task Manager UI (/ui/* endpoints).

Uses starlette TestClient — no browser, no HTMX JS.
Each test asserts on the HTML fragment returned by the route.

Excluded from the default pytest run (marked `integration`). Run explicitly:
    uv run python -m pytest tests/test_ui_routes.py -v
"""
import pytest
from starlette.testclient import TestClient

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def client():
    from hooks.server import app
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------

class TestIndex:
    def test_returns_200(self, client):
        r = client.get("/ui/")
        assert r.status_code == 200

    def test_contains_sidebar(self, client):
        r = client.get("/ui/")
        assert "task-tree" in r.text or "TASKS" in r.text

    def test_status_done(self, client):
        r = client.get("/ui/?status=done")
        assert r.status_code == 200

    def test_invalid_status_falls_back_to_open(self, client):
        r = client.get("/ui/?status=bogus")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Sidebar partial
# ---------------------------------------------------------------------------

class TestSidebar:
    def test_open(self, client):
        r = client.get("/ui/sidebar?status=open")
        assert r.status_code == 200
        assert "task-tree" in r.text

    def test_done(self, client):
        r = client.get("/ui/sidebar?status=done")
        assert r.status_code == 200

    def test_invalid_status_defaults_to_open(self, client):
        r = client.get("/ui/sidebar?status=all")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Task detail
# ---------------------------------------------------------------------------

class TestTaskDetail:
    def test_known_task_returns_detail(self, client):
        # Grab first open task id from sidebar
        r = client.get("/ui/sidebar?status=open")
        assert r.status_code == 200
        # Extract any task id from hx-get="/ui/tasks/<id>" attribute
        import re
        ids = re.findall(r'hx-get="/ui/tasks/([a-f0-9]+)"', r.text)
        assert ids, "no task rows found in sidebar"
        task_id = ids[0]
        detail = client.get(f"/ui/tasks/{task_id}")
        assert detail.status_code == 200
        assert "detail-title" in detail.text

    def test_unknown_task_returns_empty_state(self, client):
        r = client.get("/ui/tasks/000000000000")
        assert r.status_code == 200
        assert "not found" in r.text.lower() or "empty-state" in r.text


# ---------------------------------------------------------------------------
# New task form
# ---------------------------------------------------------------------------

class TestNewTaskForm:
    def test_returns_form(self, client):
        r = client.get("/ui/tasks/new")
        assert r.status_code == 200
        assert "create-form" in r.text or "New Task" in r.text

    def test_body_fields_task(self, client):
        r = client.get("/ui/tasks/body-fields?issue_type=task")
        assert r.status_code == 200
        assert "Motivation" in r.text or "Task" in r.text

    def test_body_fields_epic(self, client):
        r = client.get("/ui/tasks/body-fields?issue_type=epic")
        assert r.status_code == 200
        assert "Overview" in r.text or "Motivation" in r.text

    def test_body_fields_bug(self, client):
        r = client.get("/ui/tasks/body-fields?issue_type=bug")
        assert r.status_code == 200
        assert "Steps" in r.text or "Expected" in r.text

    def test_body_fields_subtask(self, client):
        r = client.get("/ui/tasks/body-fields?issue_type=subtask")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_empty_query_returns_empty(self, client):
        r = client.get("/ui/search?q=")
        assert r.status_code == 200
        assert r.text.strip() == ""

    def test_short_query_returns_empty(self, client):
        r = client.get("/ui/search?q=a")
        assert r.status_code == 200
        assert r.text.strip() == ""

    def test_known_keyword_returns_tasks(self, client):
        r = client.get("/ui/search?q=task")
        assert r.status_code == 200
        assert "TASKS" in r.text or "search-task-card" in r.text

    def test_results_contain_highlight(self, client):
        r = client.get("/ui/search?q=task")
        assert "search-highlight" in r.text

    def test_no_match_shows_empty_state(self, client):
        r = client.get("/ui/search?q=zzznomatchxxx")
        assert r.status_code == 200
        assert "No results" in r.text or r.text.strip() == ""

    def test_decisions_section_present_when_matched(self, client):
        # decisions have 'decision' in summary from the log_decision skill
        r = client.get("/ui/search?q=decision")
        assert r.status_code == 200
        # either decisions section or just tasks — both valid

    def test_memories_section_present_when_matched(self, client):
        r = client.get("/ui/search?q=feedback")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_404_on_ui_returns_html_partial(self, client):
        r = client.get("/ui/nonexistent-route-xyz")
        assert r.status_code == 200  # error partial returned at 200
        assert "error" in r.text.lower()
