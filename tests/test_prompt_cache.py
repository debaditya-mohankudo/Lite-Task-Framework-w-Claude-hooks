from pathlib import Path

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


def test_exact_match_reports_match_type_exact():
    pc.store_cache("how is context built for a task", "answer one")
    row = pc.lookup_cache("How Is Context Built For A Task?")
    assert row["match_type"] == "exact"


def _seed_realistic_corpus():
    # BM25 IDF degenerates on tiny (1-2 doc) corpora — log((N-n+0.5)/(n+0.5)) hits
    # an exact log(1)=0 edge case at N=2, n=1. A handful of filler entries avoids
    # that pathology and mirrors the live cache's corpus size where this was validated.
    pc.store_cache("what context is loaded into an active task", "the four sections answer")
    pc.store_cache("how does copilot cli work", "unrelated answer one")
    pc.store_cache("how does the ups pipeline work", "unrelated answer two")
    pc.store_cache("what is the difference between copilot and claude code", "unrelated answer three")
    pc.store_cache("how to use mlx", "unrelated answer four")


def test_bm25_fallback_catches_paraphrase():
    _seed_realistic_corpus()
    row = pc.lookup_cache("how does context get built for an active task")
    assert row is not None
    assert row["match_type"] == "fuzzy"
    assert row["cache"] == "the four sections answer"


def test_bm25_fallback_returns_none_for_unrelated_query():
    pc.store_cache("what context is loaded into an active task", "answer")
    assert pc.lookup_cache("what is the weather in tokyo") is None


def test_bm25_fallback_returns_none_on_empty_corpus():
    assert pc.lookup_cache("anything at all") is None


def test_bm25_fallback_never_overrides_exact_match():
    pc.store_cache("how does ups flow work", "exact answer")
    pc.store_cache("how does the ups pipeline function overall in this system", "other answer")
    row = pc.lookup_cache("how does ups flow work")
    assert row["match_type"] == "exact"
    assert row["cache"] == "exact answer"


def test_handle_lookup_includes_match_type():
    _seed_realistic_corpus()
    result = pc.handle_lookup("how does context get built for an active task")
    assert result["hit"] is True
    assert result["match_type"] == "fuzzy"


def test_list_cache_returns_all_entries_no_body():
    pc.store_cache("how does ups flow work", "the answer body", tags="ups,flow")
    rows = pc.list_cache()
    assert len(rows) == 1
    assert rows[0]["prompt"] == "how does ups flow work"
    assert "cache" not in rows[0]


def test_list_cache_filters_by_source():
    pc.store_cache("code question", "a", source="code")
    pc.store_cache("web question", "b", source="websearch")
    rows = pc.list_cache(source="websearch")
    assert len(rows) == 1
    assert rows[0]["prompt"] == "web question"


def test_list_cache_filters_by_tags_substring():
    pc.store_cache("q1", "a", tags="task-framework,hooks")
    pc.store_cache("q2", "b", tags="mlx,ollama")
    rows = pc.list_cache(tags="task-framework")
    assert len(rows) == 1
    assert rows[0]["prompt"] == "q1"


def test_list_cache_orders_by_last_updated_desc(monkeypatch):
    pc.store_cache("older", "a")
    pc.store_cache("newer", "b")
    rows = pc.list_cache()
    assert [r["prompt"] for r in rows] == ["newer", "older"]


def test_list_cache_empty_corpus():
    assert pc.list_cache() == []


def test_handle_list_wraps_count_and_results():
    pc.store_cache("q1", "a")
    result = pc.handle_list()
    assert result["count"] == 1
    assert result["results"][0]["prompt"] == "q1"


def test_search_cache_finds_paraphrase_matches():
    _seed_realistic_corpus()
    rows = pc.search_cache("how does context get built for an active task")
    assert any(r["prompt"] == "what context is loaded into an active task" for r in rows)


def test_search_cache_excludes_low_score_matches():
    pc.store_cache("how does ups flow work", "a")
    rows = pc.search_cache("completely unrelated gardening tips")
    assert rows == []


def test_search_cache_includes_score_field():
    _seed_realistic_corpus()
    rows = pc.search_cache("how does the ups pipeline work")
    assert rows[0]["score"] > 0


def test_search_cache_empty_corpus():
    assert pc.search_cache("anything") == []


def test_handle_search_wraps_count_and_results():
    _seed_realistic_corpus()
    result = pc.handle_search("how does the ups pipeline work")
    assert result["count"] >= 1
    assert result["results"][0]["prompt"] == "how does the ups pipeline work"


# --- domain scoping (task:91dad030) -----------------------------------------


def test_domain_from_cwd_matches_known_project(monkeypatch):
    monkeypatch.setattr(
        pc, "_domain_from_cwd",
        lambda cwd: {"claude-hooks-dev": "claude-hooks"}.get(Path(cwd).name) if cwd else None,
    )
    assert pc._domain_from_cwd("/Users/debaditya/workspace/claude-hooks-dev") == "claude-hooks"


def test_domain_from_cwd_returns_none_for_empty_cwd():
    assert pc._domain_from_cwd("") is None


def test_domain_from_cwd_returns_none_for_unmapped_path():
    assert pc._domain_from_cwd("/tmp/some/totally/unmapped/path/xyz") is None


def test_store_explicit_domain_overrides_cwd_inference(monkeypatch):
    monkeypatch.setattr(pc, "_domain_from_cwd", lambda cwd: "inferred-domain")
    result = pc.store_cache("some prompt", "some answer", domain="explicit-domain", cwd="/some/path")
    assert result["domain"] == "explicit-domain"


def test_store_infers_domain_from_cwd_when_domain_not_given(monkeypatch):
    monkeypatch.setattr(pc, "_domain_from_cwd", lambda cwd: "inferred-domain")
    result = pc.store_cache("some prompt", "some answer", cwd="/some/path")
    assert result["domain"] == "inferred-domain"


def test_store_defaults_domain_to_empty_string():
    result = pc.store_cache("some prompt", "some answer")
    assert result["domain"] == ""


def test_lookup_includes_domain_field():
    pc.store_cache("some prompt", "some answer", domain="seniordevagent")
    row = pc.lookup_cache("some prompt")
    assert row["domain"] == "seniordevagent"


def test_lookup_is_not_scoped_by_domain():
    # lookup_cache stays global by design — a hit from one domain must still be
    # found by a caller that never mentions domain at all.
    pc.store_cache("cross domain prompt", "answer", domain="repo-a")
    row = pc.lookup_cache("cross domain prompt")
    assert row is not None
    assert row["cache"] == "answer"


def test_list_cache_filters_by_domain():
    pc.store_cache("q1", "a", domain="seniordevagent")
    pc.store_cache("q2", "b", domain="claude-hooks")
    rows = pc.list_cache(domain="seniordevagent")
    assert len(rows) == 1
    assert rows[0]["prompt"] == "q1"


def test_list_cache_domain_filter_excludes_unscoped_legacy_entries():
    pc.store_cache("legacy entry", "a")  # no domain, mirrors pre-migration rows
    pc.store_cache("scoped entry", "b", domain="seniordevagent")
    rows = pc.list_cache(domain="seniordevagent")
    assert [r["prompt"] for r in rows] == ["scoped entry"]


def test_search_cache_filters_by_domain():
    # Filler entries in the same domain avoid the tiny-corpus BM25/IDF degeneracy
    # documented in _seed_realistic_corpus above.
    pc.store_cache("how does the ups pipeline work", "a", domain="claude-hooks")
    pc.store_cache("how does copilot cli work", "filler one", domain="claude-hooks")
    pc.store_cache("how to use mlx", "filler two", domain="claude-hooks")
    pc.store_cache("how does the ups pipeline work in other repo", "b", domain="seniordevagent")
    rows = pc.search_cache("how does the ups pipeline work", domain="claude-hooks")
    prompts = [r["prompt"] for r in rows]
    assert "how does the ups pipeline work" in prompts
    assert "how does the ups pipeline work in other repo" not in prompts


def test_handle_store_passes_domain_and_cwd_through(monkeypatch):
    monkeypatch.setattr(pc, "_domain_from_cwd", lambda cwd: "inferred-domain")
    result = pc.handle_store("some prompt", "some answer", cwd="/some/path")
    assert result["domain"] == "inferred-domain"


def test_handle_list_filters_by_domain():
    pc.store_cache("q1", "a", domain="seniordevagent")
    pc.store_cache("q2", "b", domain="claude-hooks")
    result = pc.handle_list(domain="seniordevagent")
    assert result["count"] == 1
    assert result["results"][0]["prompt"] == "q1"
