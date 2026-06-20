"""Tests for LoadRelatedCommitsNode — diff_rag semantic search via TurboVec."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from langchain_learning.nodes.load_related_commits import LoadRelatedCommitsNode


def _state(task_id: str = "aaaaaaaa", title: str = "some task", body: str = "fix the thing") -> dict:
    return {"active_task_id": task_id, "active_task_title": title, "task_body": body, "session_id": "test"}


def _hits(n: int = 3) -> list[dict]:
    return [
        {"commit_hash": f"abc{i}def0", "file": f"src/foo{i}.py", "score": round(0.9 - i * 0.1, 1), "snippet": f"+ line {i}"}
        for i in range(n)
    ]


def test_no_active_task_returns_empty():
    node = LoadRelatedCommitsNode()
    result = node({"active_task_id": "", "active_task_title": "", "task_body": "", "session_id": "test"})
    assert result == {"related_commits": []}


def test_returns_top_3_commits(tmp_path):
    mock_index = MagicMock()
    mock_meta  = {"0": _hits(1)[0], "1": _hits(2)[1], "2": _hits(3)[2], "__indexed_commits__": []}

    with patch("langchain_learning.nodes.load_related_commits.load_index", return_value=(mock_index, mock_meta)), \
         patch("langchain_learning.nodes.load_related_commits.query_index", return_value=_hits(3)), \
         patch("langchain_learning.nodes.load_related_commits.OllamaEmbedding") as mock_embed:
        mock_embed.return_value.get_text_embedding.return_value = [0.1] * 768
        node = LoadRelatedCommitsNode()
        result = node(_state())

    commits = result["related_commits"]
    assert len(commits) == 3
    assert all("commit_hash" in c for c in commits)
    assert all("file" in c for c in commits)
    assert all("score" in c for c in commits)


def test_commit_hash_truncated_to_8_chars():
    with patch("langchain_learning.nodes.load_related_commits.load_index", return_value=(MagicMock(), {"__indexed_commits__": []})), \
         patch("langchain_learning.nodes.load_related_commits.query_index", return_value=[
             {"commit_hash": "abcdef1234567890", "file": "src/x.py", "score": 0.95, "snippet": "+ x"}
         ]), \
         patch("langchain_learning.nodes.load_related_commits.OllamaEmbedding") as mock_embed:
        mock_embed.return_value.get_text_embedding.return_value = [0.1] * 768
        node = LoadRelatedCommitsNode()
        result = node(_state())

    assert result["related_commits"][0]["commit_hash"] == "abcdef12"


def test_index_missing_returns_empty():
    with patch("langchain_learning.nodes.load_related_commits.load_index", return_value=(None, {})):
        node = LoadRelatedCommitsNode()
        result = node(_state())
    assert result == {"related_commits": []}


def test_query_error_returns_empty():
    with patch("langchain_learning.nodes.load_related_commits.load_index", return_value=(MagicMock(), {"__indexed_commits__": []})), \
         patch("langchain_learning.nodes.load_related_commits.query_index", side_effect=Exception("tvim error")), \
         patch("langchain_learning.nodes.load_related_commits.OllamaEmbedding") as mock_embed:
        mock_embed.return_value.get_text_embedding.return_value = [0.1] * 768
        node = LoadRelatedCommitsNode()
        result = node(_state())
    assert result == {"related_commits": []}


def test_empty_title_and_body_returns_empty():
    node = LoadRelatedCommitsNode()
    result = node({"active_task_id": "aaaaaaaa", "active_task_title": "", "task_body": "", "session_id": "test"})
    assert result == {"related_commits": []}


def test_snippet_truncated_to_200_chars():
    long_snippet = "+" + "x" * 300
    with patch("langchain_learning.nodes.load_related_commits.load_index", return_value=(MagicMock(), {"__indexed_commits__": []})), \
         patch("langchain_learning.nodes.load_related_commits.query_index", return_value=[
             {"commit_hash": "abc00000", "file": "src/x.py", "score": 0.9, "snippet": long_snippet}
         ]), \
         patch("langchain_learning.nodes.load_related_commits.OllamaEmbedding") as mock_embed:
        mock_embed.return_value.get_text_embedding.return_value = [0.1] * 768
        node = LoadRelatedCommitsNode()
        result = node(_state())

    assert len(result["related_commits"][0]["snippet"]) <= 200
