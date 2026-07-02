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
import subprocess
from pathlib import Path
from typing import Optional

_DB = Path.home() / ".claude" / "prompt_cache.sqlite"

# Pinned to the repo this module lives in — NOT the caller's ambient cwd. The MCP
# server process inherits whatever cwd Claude Code launched it with, which can be
# a different worktree (e.g. -dev) than the code that's actually running (main),
# so `git rev-parse HEAD` with no cwd override silently tracks the wrong repo.
_REPO_ROOT = Path(__file__).resolve().parents[2]

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
            commit_sha TEXT DEFAULT '',
            last_updated TIMESTAMP DEFAULT (datetime('now'))
        )
        """
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(prompt_cache)")}
    if "commit_sha" not in cols:
        conn.execute("ALTER TABLE prompt_cache ADD COLUMN commit_sha TEXT DEFAULT ''")
    conn.commit()
    return conn


def _git(*args: str, cwd: Optional[Path] = None) -> str:
    """Run a git command in `cwd` (default: current working directory). '' on any failure."""
    try:
        result = subprocess.run(
            ["git", *args], cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=5, check=True,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _current_commit_sha(cwd: Optional[Path] = None) -> str:
    """Short SHA of the repo's HEAD — the "tip of commit" the answer was cached at.

    Defaults to `_REPO_ROOT` (this module's own repo), not the caller's ambient
    cwd — an MCP server process inherits whatever cwd it was launched with, which
    may be a different worktree than the code actually running.
    """
    return _git("rev-parse", "--short", "HEAD", cwd=cwd or _REPO_ROOT)


def _commits_behind(commit_sha: str, cwd: Optional[Path] = None) -> Optional[int]:
    """Number of commits between `commit_sha` and HEAD in the repo at `cwd` (default `_REPO_ROOT`).

    Staleness is measured relative to commits, not wall-clock time — a cache entry
    from 10 minutes ago right before 5 commits landed is staler than one from a
    week ago on a quiet branch. Returns None if `commit_sha` is unknown/unset or
    not an ancestor resolvable in this repo (e.g. cached from a different repo).
    """
    if not commit_sha:
        return None
    count = _git("rev-list", "--count", f"{commit_sha}..HEAD", cwd=cwd or _REPO_ROOT)
    return int(count) if count.isdigit() else None


def lookup_cache(prompt: str) -> Optional[dict]:
    """Normalize `prompt` and look up an exact match. Returns the row as a dict, or None.

    The returned dict includes `commits_behind` (int or None) — how many commits have
    landed since this entry was cached, relative to the current repo HEAD — so callers
    can build a staleness-warning confirmation without a second query. Also includes
    `age_days` as a secondary, less meaningful signal.
    """
    key = normalize_prompt(prompt)
    if not key:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT prompt, cache, tags, commit_sha, last_updated, "
            "julianday('now') - julianday(last_updated) AS age_days "
            "FROM prompt_cache WHERE prompt = ?",
            (key,),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["commits_behind"] = _commits_behind(result["commit_sha"])
    return result


def store_cache(prompt: str, cache: str, tags: str = "") -> dict:
    """Normalize `prompt` and upsert a cache row, tagged with the current repo's HEAD
    commit (the "tip of commit" at cache time) so later lookups can tell how many
    commits have landed since. Returns the stored row's key fields.
    """
    key = normalize_prompt(prompt)
    if not key:
        return {"error": "prompt normalizes to empty string — nothing to cache"}
    commit_sha = _current_commit_sha()
    with _connect() as conn:
        conn.execute(
            "INSERT INTO prompt_cache (prompt, cache, tags, commit_sha, last_updated) "
            "VALUES (?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(prompt) DO UPDATE SET "
            "cache=excluded.cache, tags=excluded.tags, commit_sha=excluded.commit_sha, "
            "last_updated=excluded.last_updated",
            (key, cache, tags, commit_sha),
        )
        conn.commit()
    return {"prompt": key, "tags": tags, "commit_sha": commit_sha}


# ---------------------------------------------------------------------------
# MCP tool entry points (registered as prompt_cache__lookup / prompt_cache__store
# in src/dispatcher.py). Thin wrappers so Claude itself can query/write the cache
# mid-session — e.g. before re-deriving an answer to a design/spec question it
# has already answered in this or a prior session.
# ---------------------------------------------------------------------------

def handle_lookup(prompt: str) -> dict:
    """Look up a cached answer for `prompt` (normalize + exact match).

    Never serve the result silently — surface a confirmation to the user first,
    e.g. "This looks cached (N commits behind HEAD) — want to see the cached
    answer?" using the returned `commits_behind`. Staleness is measured in
    commits, not wall-clock time — `age_days` is a secondary signal only.

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
