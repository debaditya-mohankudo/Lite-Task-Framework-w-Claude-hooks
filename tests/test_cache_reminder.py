import pytest

from hooks.dispatcher import _maybe_cache_reminder, _CACHE_REMINDER_SHOWN


@pytest.fixture(autouse=True)
def _clear_reminder_state():
    _CACHE_REMINDER_SHOWN.clear()
    yield
    _CACHE_REMINDER_SHOWN.clear()


def test_fires_on_write():
    result = _maybe_cache_reminder("Write", "session-a")
    assert result is not None
    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
    assert "prompt_cache" in result["hookSpecificOutput"]["additionalContext"]


def test_fires_on_edit_and_multiedit():
    assert _maybe_cache_reminder("Edit", "session-b") is not None
    assert _maybe_cache_reminder("MultiEdit", "session-c") is not None


def test_does_not_fire_for_unrelated_tools():
    assert _maybe_cache_reminder("Read", "session-d") is None
    assert _maybe_cache_reminder("Bash", "session-d") is None
    assert _maybe_cache_reminder("tasks__create", "session-d") is None


def test_only_fires_once_per_session():
    first = _maybe_cache_reminder("Write", "session-e")
    second = _maybe_cache_reminder("Edit", "session-e")
    assert first is not None
    assert second is None


def test_fires_independently_per_session():
    assert _maybe_cache_reminder("Write", "session-f") is not None
    assert _maybe_cache_reminder("Write", "session-g") is not None
