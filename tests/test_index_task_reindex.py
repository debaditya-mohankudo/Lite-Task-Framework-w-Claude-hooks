"""Regression test for task:9c1a387f — handle_index_task must upsert (not raise)
when re-indexing a task that's already present in the TurboVec index."""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pytest

from src.db.schema import OPEN_TASKS_DDL
from src.tools.tasks import handle_index_task


class _StubEmbedModel:
    """Deterministic embedding so index dimensions match across calls without
    hitting a real Ollama server."""

    def get_text_embedding(self, text: str) -> list[float]:
        # turbovec.IdMapIndex requires dim to be a positive multiple of 8.
        return [float((len(text) + i) % 7) for i in range(8)]


@pytest.fixture
def isolated_task_index(tmp_path):
    db = tmp_path / "proj_tasks.db"
    tvim = tmp_path / "tasks_embeddings.tvim"
    meta = tmp_path / "tasks_embeddings.meta.json"

    with sqlite3.connect(db) as conn:
        conn.execute(OPEN_TASKS_DDL)
        conn.execute(
            "INSERT INTO open_tasks (id, title, body, status, tags) VALUES (?, ?, ?, ?, ?)",
            ("abc123", "Test task", "Type: misc\nTask: x", "open", ""),
        )
        conn.commit()

    with (
        patch("src.tools.tasks._DB", db),
        patch("src.tools.tasks._TASKS_TVIM", tvim),
        patch("src.tools.tasks._TASKS_META", meta),
        patch("src.tools.tasks._get_embed_model", return_value=_StubEmbedModel()),
    ):
        yield db


def test_reindexing_same_task_does_not_raise(isolated_task_index):
    first = handle_index_task("abc123")
    assert first.get("ok") is True

    # This previously raised "id <n> already present in index" instead of
    # replacing the existing vector.
    second = handle_index_task("abc123")
    assert second.get("ok") is True


def test_reindex_does_not_duplicate_meta_entries(isolated_task_index):
    from src.tools.rag_core import load_index
    from src.tools.tasks import _TASKS_META, _TASKS_TVIM, _task_uid

    handle_index_task("abc123")
    handle_index_task("abc123")

    _, meta = load_index(_TASKS_TVIM, _TASKS_META)
    uid = str(_task_uid("abc123"))
    assert uid in meta
    assert meta[uid]["task_id"] == "abc123"
