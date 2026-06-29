"""Unit tests for concept_store/extractor.py using a fake Anthropic client."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from concept_store.extractor import extract
from concept_store.store import ConceptStore

_FAKE_CONCEPTS = [
    {
        "name": "dispatcher-routes-by-hook-type",
        "module": "hooks/dispatcher.py",
        "description": "Routes hook events to handler nodes based on event_type.",
        "invariants": ["all hooks must return HookResult"],
        "contracts": ["returns dict or None"],
        "confidence": 0.9,
        "evidence": ["hooks/dispatcher.py:42"],
    },
    {
        "name": "gates-prereq-chain",
        "module": "hooks/gates.py",
        "description": "Chains prerequisite verifiers before allowing tool execution.",
        "invariants": ["gate failures block tool execution"],
        "contracts": ["returns GateResult with allow bool"],
        "confidence": 0.85,
        "evidence": ["hooks/gates.py:80"],
    },
]


def _make_fake_client(response_json: list) -> MagicMock:
    content_block = MagicMock()
    content_block.text = json.dumps(response_json)
    message = MagicMock()
    message.content = [content_block]
    client = MagicMock()
    client.messages.create.return_value = message
    return client


def test_extract_upserts_all_concepts(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    client = _make_fake_client(_FAKE_CONCEPTS)
    concepts = extract(tmp_path, store, client=client)
    assert len(concepts) == 2
    assert store.get("dispatcher-routes-by-hook-type") is not None
    assert store.get("gates-prereq-chain") is not None


def test_extract_calls_claude_once(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    client = _make_fake_client(_FAKE_CONCEPTS)
    extract(tmp_path, store, client=client)
    assert client.messages.create.call_count == 1


def test_extract_raises_on_bad_json(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    content_block = MagicMock()
    content_block.text = "not json at all"
    message = MagicMock()
    message.content = [content_block]
    client = MagicMock()
    client.messages.create.return_value = message
    with pytest.raises(ValueError, match="unparseable JSON"):
        extract(tmp_path, store, client=client)


def test_extract_raises_on_non_array(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    client = _make_fake_client({"not": "an array"})
    with pytest.raises(ValueError, match="Expected JSON array"):
        extract(tmp_path, store, client=client)


def test_concepts_persisted_to_json(tmp_path):
    store = ConceptStore(tmp_path / "concepts.json")
    client = _make_fake_client(_FAKE_CONCEPTS)
    extract(tmp_path, store, client=client)
    store2 = ConceptStore(tmp_path / "concepts.json")
    assert len(store2.list()) == 2
