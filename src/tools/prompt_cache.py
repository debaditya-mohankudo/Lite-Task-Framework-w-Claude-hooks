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
            source TEXT DEFAULT 'code',
            last_updated TIMESTAMP DEFAULT (datetime('now'))
        )
        """
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(prompt_cache)")}
    if "commit_sha" not in cols:
        conn.execute("ALTER TABLE prompt_cache ADD COLUMN commit_sha TEXT DEFAULT ''")
    if "source" not in cols:
        conn.execute("ALTER TABLE prompt_cache ADD COLUMN source TEXT DEFAULT 'code'")
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

    The returned dict includes `source` ("code" or "websearch") which determines which
    staleness signal is meaningful:
      - source="code":      use `commits_behind` — how many commits have landed in this
                             repo since caching. `age_days` is a secondary signal only.
      - source="websearch": `commits_behind` is meaningless (external facts don't move
                             in lockstep with this repo's commits) — use `age_days` instead.
    """
    key = normalize_prompt(prompt)
    if not key:
        return None
    with _connect() as conn:
        row = conn.execute(
            "SELECT prompt, cache, tags, commit_sha, source, last_updated, "
            "julianday('now') - julianday(last_updated) AS age_days "
            "FROM prompt_cache WHERE prompt = ?",
            (key,),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["commits_behind"] = _commits_behind(result["commit_sha"]) if result["source"] == "code" else None
    return result


def store_cache(prompt: str, cache: str, tags: str = "", source: str = "code") -> dict:
    """Normalize `prompt` and upsert a cache row. Returns the stored row's key fields.

    Args:
        source: "code" (default) — tags the entry with the current repo's HEAD commit
                (the "tip of commit" at cache time) so later lookups can tell how many
                commits have landed since. Use "websearch" for answers sourced from
                WebSearch/WebFetch, where commit-based staleness doesn't apply —
                commit_sha is left empty and callers should rely on age_days instead.
    """
    key = normalize_prompt(prompt)
    if not key:
        return {"error": "prompt normalizes to empty string — nothing to cache"}
    commit_sha = _current_commit_sha() if source == "code" else ""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO prompt_cache (prompt, cache, tags, commit_sha, source, last_updated) "
            "VALUES (?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(prompt) DO UPDATE SET "
            "cache=excluded.cache, tags=excluded.tags, commit_sha=excluded.commit_sha, "
            "source=excluded.source, last_updated=excluded.last_updated",
            (key, cache, tags, commit_sha, source),
        )
        conn.commit()
    return {"prompt": key, "tags": tags, "commit_sha": commit_sha, "source": source}


def delete_cache(prompt: str) -> dict:
    """Normalize `prompt` and delete its cache row, if any. Returns {"deleted": bool}."""
    key = normalize_prompt(prompt)
    if not key:
        return {"deleted": False}
    with _connect() as conn:
        cur = conn.execute("DELETE FROM prompt_cache WHERE prompt = ?", (key,))
        conn.commit()
    return {"deleted": cur.rowcount > 0}


# ---------------------------------------------------------------------------
# MCP tool entry points (registered as prompt_cache__lookup / prompt_cache__store /
# prompt_cache__delete in src/dispatcher.py). Thin wrappers so Claude itself can
# query/write/prune the cache mid-session — e.g. before re-deriving an answer to a
# design/spec question it has already answered, or when it notices a stored answer
# is stale enough to be actively misleading.
# ---------------------------------------------------------------------------

def handle_lookup(prompt: str) -> dict:
    """Look up a cached answer for `prompt` (normalize + exact match).

    Never serve the result silently — surface a confirmation to the user first.
    Check `source` to pick the right staleness signal:
      - source="code":      "This looks cached (N commits behind HEAD) — want to see it?"
                             using `commits_behind`.
      - source="websearch": "This looks cached (N days old) — want to see it?"
                             using `age_days` (`commits_behind` is null/meaningless here).

    Args:
        prompt: The question/prompt text to look up (normalized before matching).
    """
    row = lookup_cache(prompt)
    if row is None:
        return {"hit": False}
    return {"hit": True, **row}


def handle_store(prompt: str, cache: str, tags: str = "", source: str = "code") -> dict:
    """Store or refresh a cached answer for `prompt`.

    Call this after answering a question worth remembering — especially recurring
    "how does X work?" / design-spec questions during feature development, or facts
    looked up via WebSearch/WebFetch — so a future identical question can offer the
    cached answer instead of re-deriving it.

    Anti-pollution rule: only store answers that touch 3+ distinct concepts/modules
    (e.g. an architectural explanation spanning several files/subsystems, or a
    multi-source research answer). Do not cache single-concept or trivial factual
    answers — that turns this into general scratch storage and drowns out the
    genuinely expensive-to-reconstruct entries.

    Args:
        prompt: The question/prompt text (normalized before storing).
        cache:  The answer to cache.
        tags:   Optional comma-separated keywords for the entry.
        source: "code" (default, ties the entry to this repo's commit history) or
                "websearch" (for answers not tied to this repo's code — general
                tool/library comparisons, external facts — staleness tracked by
                age_days instead of commits_behind).
    """
    return store_cache(prompt, cache, tags, source)


def handle_delete(prompt: str) -> dict:
    """Delete a cached answer for `prompt` (normalize + exact match).

    Use this to prune entries that have become stale enough to be actively
    misleading — e.g. a "code" entry with a very high `commits_behind` where
    the underlying code has clearly changed, or an entry that turned out not
    to be worth keeping. Confirm with the user before deleting anything they
    might still want, unless they explicitly asked for the deletion.

    Args:
        prompt: The question/prompt text to delete (normalized before matching).
    """
    return delete_cache(prompt)
