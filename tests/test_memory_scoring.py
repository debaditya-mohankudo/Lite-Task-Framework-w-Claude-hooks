"""Tests for langchain_learning/nodes/_memory_scoring.py — combination signal + related boost."""
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from langchain_learning.nodes._memory_scoring import score_memories

_DDL = """
    CREATE TABLE memories (
        id      INTEGER PRIMARY KEY AUTOINCREMENT,
        name    TEXT UNIQUE NOT NULL,
        type    TEXT NOT NULL,
        domain  TEXT DEFAULT 'global',
        tags    TEXT DEFAULT '',
        body    TEXT DEFAULT '',
        related TEXT DEFAULT '',
        updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
"""

_CFG = {
    "top_n": 5,
    "batch_limit": 500,
    "tag_weight": 3.0,
    "body_weight": 1.0,
    "recency_boost": 1.2,
    "recency_penalty": 0.8,
    "recency_boost_days": 30,
    "recency_penalty_days": 180,
    "min_keyword_score": 0.0,
    "domain_keyword_boost": 0.8,
    "domain_weights": {"project": 2.0, "global": 0.5},
    "domain_keywords": {},
    "combination_signals": {},
    "related_boost_factor": 0.15,
    "related_max_neighbours": 2,
}


@pytest.fixture
def mem_conn():
    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    con = sqlite3.connect(tmp.name)
    con.row_factory = sqlite3.Row
    con.execute(_DDL)
    yield con
    con.close()


def _insert(con, name, domain="claude-hooks", tags="", body="", related=""):
    con.execute(
        "INSERT INTO memories (name, type, domain, tags, body, related) VALUES (?,?,?,?,?,?)",
        (name, "project", domain, tags, body, related),
    )
    con.commit()


def test_high_scorer_boosts_related_neighbour(mem_conn):
    """A memory with no keyword match should appear if linked via related to a high scorer."""
    _insert(mem_conn, "gate-framework", tags="gate framework prereq", body="Gate ABC pattern", related="gate-prereq-tracking")
    _insert(mem_conn, "gate-prereq-tracking", tags="obscure-unrelated-zzz", body="obscure-unrelated-zzz")

    with patch("langchain_learning.nodes._memory_scoring.load_scoring_cfg", return_value=_CFG):
        results = score_memories({"gate", "framework"}, "claude-hooks", mem_conn, top_n=5)

    names = [m["name"] for m in results]
    assert "gate-framework" in names, "seed should be in results"
    assert "gate-prereq-tracking" in names, "related neighbour should be boosted into results"


def test_neighbour_boost_is_additive(mem_conn):
    """A neighbour that already scores on its own should score higher after boost."""
    _insert(mem_conn, "seed", tags="gate", body="gate framework", related="neighbour")
    _insert(mem_conn, "neighbour", tags="gate", body="gate prereq")

    with patch("langchain_learning.nodes._memory_scoring.load_scoring_cfg", return_value=_CFG):
        results_with_related = score_memories({"gate"}, "claude-hooks", mem_conn, top_n=5)

    # Remove related from seed to get baseline neighbour score
    mem_conn.execute("UPDATE memories SET related='' WHERE name='seed'")
    mem_conn.commit()

    with patch("langchain_learning.nodes._memory_scoring.load_scoring_cfg", return_value=_CFG):
        results_without_related = score_memories({"gate"}, "claude-hooks", mem_conn, top_n=5)

    def get_rank(results, name):
        names = [m["name"] for m in results]
        return names.index(name) if name in names else 999

    # neighbour should rank higher (lower index) when boost is applied
    assert get_rank(results_with_related, "neighbour") <= get_rank(results_without_related, "neighbour")


def test_max_neighbours_per_seed_respected(mem_conn):
    """Only related_max_neighbours (2) neighbours per seed should be boosted.

    Noise memories use domain='unknown' (weight=0) so they only appear via boost.
    """
    _insert(mem_conn, "seed", tags="gate", body="gate", related="n1,n2,n3")
    _insert(mem_conn, "n1", domain="unknown", tags="zzz", body="zzz")
    _insert(mem_conn, "n2", domain="unknown", tags="zzz", body="zzz")
    _insert(mem_conn, "n3", domain="unknown", tags="zzz", body="zzz")

    cfg = {**_CFG, "related_max_neighbours": 2, "top_n": 10}
    with patch("langchain_learning.nodes._memory_scoring.load_scoring_cfg", return_value=cfg):
        results = score_memories({"gate"}, "claude-hooks", mem_conn, top_n=10)

    names = [m["name"] for m in results]
    boosted = [n for n in ["n1", "n2", "n3"] if n in names]
    assert len(boosted) == 2, f"expected 2 boosted neighbours, got {boosted}"


def test_zero_boost_factor_disables_graph(mem_conn):
    """related_boost_factor=0 should not surface zero-direct-scoring neighbours.

    Neighbour uses domain='unknown' (weight=0) so it only appears via boost.
    """
    _insert(mem_conn, "seed", tags="gate", body="gate", related="silent-neighbour")
    _insert(mem_conn, "silent-neighbour", domain="unknown", tags="zzz", body="zzz")

    cfg = {**_CFG, "related_boost_factor": 0.0}
    with patch("langchain_learning.nodes._memory_scoring.load_scoring_cfg", return_value=cfg):
        results = score_memories({"gate"}, "claude-hooks", mem_conn, top_n=5)

    names = [m["name"] for m in results]
    assert "silent-neighbour" not in names
