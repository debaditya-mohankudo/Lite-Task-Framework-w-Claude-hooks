import pytest

from src.tools import prompt_cache as pc


@pytest.fixture(autouse=True)
def _tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(pc, "_DB", tmp_path / "prompt_cache.sqlite")


def test_normalize_lowercases():
    assert pc.normalize_prompt("How Does UPS Work?") == "how does ups work"


def test_normalize_strips_punctuation():
    assert pc.normalize_prompt("what's the deal, really?!") == "whats the deal really"


def test_normalize_collapses_whitespace():
    assert pc.normalize_prompt("how   does\tthis\n work") == "how does this work"


def test_normalize_strips_leading_trailing_whitespace():
    assert pc.normalize_prompt("   trim me   ") == "trim me"


def test_normalize_empty_string():
    assert pc.normalize_prompt("") == ""


def test_normalize_whitespace_only():
    assert pc.normalize_prompt("   \t\n  ") == ""


def test_normalize_idempotent_across_case_and_punctuation_variants():
    a = pc.normalize_prompt("How does UPS flow work?")
    b = pc.normalize_prompt("how does ups flow work")
    c = pc.normalize_prompt("HOW DOES UPS FLOW WORK!!!")
    assert a == b == c


def test_lookup_miss_returns_none():
    assert pc.lookup_cache("never asked this before") is None


def test_lookup_empty_prompt_returns_none():
    assert pc.lookup_cache("   ") is None


def test_store_then_lookup_exact_match():
    pc.store_cache("How does UPS flow work?", "UPS is a two-tier LangGraph fan-out.", tags="ups,langgraph")
    row = pc.lookup_cache("how does ups flow work")
    assert row is not None
    assert row["cache"] == "UPS is a two-tier LangGraph fan-out."
    assert row["tags"] == "ups,langgraph"


def test_lookup_matches_despite_case_and_punctuation_differences():
    pc.store_cache("how does ups flow work", "answer text")
    row = pc.lookup_cache("How Does UPS Flow Work?!")
    assert row is not None
    assert row["cache"] == "answer text"


def test_lookup_does_not_match_different_prompt():
    pc.store_cache("how does ups flow work", "answer text")
    assert pc.lookup_cache("how does pretooluse work") is None


def test_lookup_includes_age_days():
    pc.store_cache("some prompt", "some answer")
    row = pc.lookup_cache("some prompt")
    assert "age_days" in row
    assert row["age_days"] >= 0


def test_store_upserts_on_repeat_prompt():
    pc.store_cache("repeat me", "first answer", tags="a")
    pc.store_cache("repeat me", "second answer", tags="b")
    row = pc.lookup_cache("repeat me")
    assert row["cache"] == "second answer"
    assert row["tags"] == "b"


def test_store_empty_prompt_returns_error():
    result = pc.store_cache("   ", "answer")
    assert "error" in result


def test_handle_lookup_miss():
    result = pc.handle_lookup("nothing cached yet")
    assert result == {"hit": False}


def test_handle_lookup_hit_includes_row_fields():
    pc.store_cache("how does gates.py work", "explains gate chain", tags="gates")
    result = pc.handle_lookup("How Does gates.py Work?")
    assert result["hit"] is True
    assert result["cache"] == "explains gate chain"
    assert result["tags"] == "gates"
    assert "age_days" in result


def test_handle_store_roundtrips_through_handle_lookup():
    pc.handle_store("what is the ups pipeline", "two-tier fan-out", tags="ups")
    result = pc.handle_lookup("what is the ups pipeline")
    assert result["hit"] is True
    assert result["cache"] == "two-tier fan-out"


def test_store_captures_commit_sha(monkeypatch):
    monkeypatch.setattr(pc, "_current_commit_sha", lambda cwd=None: "abc1234")
    pc.store_cache("some prompt", "some answer")
    with pc._connect() as conn:
        row = conn.execute("SELECT commit_sha FROM prompt_cache WHERE prompt=?", ("some prompt",)).fetchone()
    assert row["commit_sha"] == "abc1234"


def test_lookup_reports_commits_behind(monkeypatch):
    monkeypatch.setattr(pc, "_current_commit_sha", lambda cwd=None: "abc1234")
    pc.store_cache("some prompt", "some answer")
    monkeypatch.setattr(pc, "_commits_behind", lambda sha, cwd=None: 5)
    row = pc.lookup_cache("some prompt")
    assert row["commits_behind"] == 5


def test_lookup_commits_behind_none_when_commit_sha_missing(monkeypatch):
    monkeypatch.setattr(pc, "_current_commit_sha", lambda cwd=None: "")
    pc.store_cache("some prompt", "some answer")
    row = pc.lookup_cache("some prompt")
    assert row["commits_behind"] is None


def test_commits_behind_returns_none_for_empty_sha():
    assert pc._commits_behind("") is None


def test_commits_behind_returns_none_for_unresolvable_sha():
    assert pc._commits_behind("not-a-real-sha-xyz") is None


def test_git_helper_returns_empty_on_failure(tmp_path):
    # Not a git repo — should not raise, just return ""
    assert pc._git("rev-parse", "--short", "HEAD", cwd=tmp_path) == ""


def test_store_defaults_to_source_code():
    result = pc.store_cache("some prompt", "some answer")
    assert result["source"] == "code"
    row = pc.lookup_cache("some prompt")
    assert row["source"] == "code"


def test_store_websearch_source_leaves_commit_sha_empty(monkeypatch):
    monkeypatch.setattr(pc, "_current_commit_sha", lambda cwd=None: "abc1234")
    result = pc.store_cache("some prompt", "some answer", source="websearch")
    assert result["commit_sha"] == ""
    row = pc.lookup_cache("some prompt")
    assert row["commit_sha"] == ""


def test_lookup_websearch_source_has_no_commits_behind(monkeypatch):
    monkeypatch.setattr(pc, "_current_commit_sha", lambda cwd=None: "abc1234")
    pc.store_cache("some prompt", "some answer", source="websearch")
    row = pc.lookup_cache("some prompt")
    assert row["commits_behind"] is None


def test_lookup_code_source_still_computes_commits_behind(monkeypatch):
    monkeypatch.setattr(pc, "_current_commit_sha", lambda cwd=None: "abc1234")
    pc.store_cache("some prompt", "some answer", source="code")
    monkeypatch.setattr(pc, "_commits_behind", lambda sha, cwd=None: 3)
    row = pc.lookup_cache("some prompt")
    assert row["commits_behind"] == 3


def test_handle_store_passes_source_through():
    result = pc.handle_store("web fact", "some external fact", source="websearch")
    assert result["source"] == "websearch"
    looked_up = pc.handle_lookup("web fact")
    assert looked_up["source"] == "websearch"
    assert looked_up["commits_behind"] is None


def test_delete_removes_existing_entry():
    pc.store_cache("delete me", "some answer")
    result = pc.delete_cache("delete me")
    assert result["deleted"] is True
    assert pc.lookup_cache("delete me") is None


def test_delete_matches_despite_case_and_punctuation_differences():
    pc.store_cache("delete me please", "some answer")
    result = pc.delete_cache("Delete Me Please!!")
    assert result["deleted"] is True


def test_delete_nonexistent_returns_false():
    result = pc.delete_cache("never cached this")
    assert result["deleted"] is False


def test_delete_empty_prompt_returns_false():
    result = pc.delete_cache("   ")
    assert result["deleted"] is False


def test_delete_does_not_affect_other_entries():
    pc.store_cache("keep me", "keep answer")
    pc.store_cache("delete me too", "delete answer")
    pc.delete_cache("delete me too")
    assert pc.lookup_cache("keep me") is not None
    assert pc.lookup_cache("delete me too") is None


def test_handle_delete_roundtrip():
    pc.handle_store("temp prompt", "temp answer")
    result = pc.handle_delete("temp prompt")
    assert result["deleted"] is True
    assert pc.handle_lookup("temp prompt") == {"hit": False}
