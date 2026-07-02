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
