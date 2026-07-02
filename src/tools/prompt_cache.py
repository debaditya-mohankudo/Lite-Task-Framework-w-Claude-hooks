"""Custom prompt-caching mechanism — epic c0f3037f.

Not Anthropic's cache_control (that's an exact byte-prefix match on the raw
request). This is a homegrown cache keyed by *normalized* prompt text, meant
to answer recurring "how does X work?" questions without re-spending
reasoning tokens, and to short-circuit repeated LLM calls inside skills
(e.g. happiest-minds-tracker's summarization step).

Match strategy: normalize (lowercase, strip punctuation, collapse whitespace)
then exact match — no embeddings, no fuzzy/edit-distance matching. A hit is
never served silently; callers must surface a confirmation ("this looks
cached, N days old — want to see it?") before using `cache`.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Optional

_DB = Path.home() / ".claude" / "prompt_cache.sqlite"

_PUNCT_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_prompt(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace. Empty/whitespace-only -> ''."""
    text = text.lower()
    text = _PUNCT_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    return text


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB), timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS prompt_cache (
            prompt TEXT PRIMARY KEY,
            cache TEXT NOT NULL,
            tags TEXT DEFAULT '',
            last_updated TIMESTAMP DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()
    return conn


def lookup_cache(prompt: str) -> Optional[dict]:
    """Normalize `prompt` and look up an exact match. Returns the row as a dict, or None.

    The returned dict includes `age_days` (float, days since last_updated) so callers
    can build the staleness-warning confirmation without a second query.
    """
    key = normalize_prompt(prompt)
    if not key:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT prompt, cache, tags, last_updated, "
            "julianday('now') - julianday(last_updated) AS age_days "
            "FROM prompt_cache WHERE prompt = ?",
            (key,),
        ).fetchone()
    return dict(row) if row else None


def store_cache(prompt: str, cache: str, tags: str = "") -> dict:
    """Normalize `prompt` and upsert a cache row. Returns the stored row's key fields."""
    key = normalize_prompt(prompt)
    if not key:
        return {"error": "prompt normalizes to empty string — nothing to cache"}
    with _connect() as conn:
        conn.execute(
            "INSERT INTO prompt_cache (prompt, cache, tags, last_updated) "
            "VALUES (?, ?, ?, datetime('now')) "
            "ON CONFLICT(prompt) DO UPDATE SET "
            "cache=excluded.cache, tags=excluded.tags, last_updated=excluded.last_updated",
            (key, cache, tags),
        )
        conn.commit()
    return {"prompt": key, "tags": tags}


# ---------------------------------------------------------------------------
# MCP tool entry points (registered as prompt_cache__lookup / prompt_cache__store
# in src/dispatcher.py). Thin wrappers so Claude itself can query/write the cache
# mid-session — e.g. before re-deriving an answer to a design/spec question it
# has already answered in this or a prior session.
# ---------------------------------------------------------------------------

def handle_lookup(prompt: str) -> dict:
    """Look up a cached answer for `prompt` (normalize + exact match).

    Never serve the result silently — surface a confirmation to the user first,
    e.g. "This looks cached (N days old) — want to see the cached answer?"
    using the returned `age_days`.

    Args:
        prompt: The question/prompt text to look up (normalized before matching).
    """
    row = lookup_cache(prompt)
    if row is None:
        return {"hit": False}
    return {"hit": True, **row}


def handle_store(prompt: str, cache: str, tags: str = "") -> dict:
    """Store or refresh a cached answer for `prompt`.

    Call this after answering a question worth remembering — especially recurring
    "how does X work?" / design-spec questions during feature development — so a
    future identical question can offer the cached answer instead of re-deriving it.

    Args:
        prompt: The question/prompt text (normalized before storing).
        cache:  The answer to cache.
        tags:   Optional comma-separated keywords for the entry.
    """
    return store_cache(prompt, cache, tags)
