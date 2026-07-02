"""Custom prompt-caching mechanism — epic c0f3037f.

Not Anthropic's cache_control (that's an exact byte-prefix match on the raw
request). This is a homegrown cache keyed by *normalized* prompt text, meant
to answer recurring "how does X work?" questions without re-spending
reasoning tokens, and to short-circuit repeated LLM calls inside skills
(e.g. happiest-minds-tracker's summarization step).

Match strategy: normalize (lowercase, strip punctuation, collapse whitespace)
then exact match FIRST — cheap and precise. If that misses, BM25 (rank-bm25,
already a repo dependency — same library used by load_memories.py/score_tools.py)
runs as a backup fallback over all cached prompts, to catch paraphrases exact-match
would otherwise miss (validated live: "how is context build for a task" vs "what
context is loaded into an active task" — same underlying question, different
wording, exact-match treats them as unrelated keys). A hit is never served
silently; callers must surface a confirmation ("this looks cached, N days old —
want to see it?") before using `cache`, and a BM25 fallback hit should say so
explicitly (it's a fuzzy match, not the exact question asked).
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional

from rank_bm25 import BM25Okapi

_DB = Path.home() / ".claude" / "prompt_cache.sqlite"

# Below this BM25 score, a "match" is noise, not a real paraphrase — validated
# against the live cache: unrelated entries scored 0.0-0.7, genuine paraphrases
# scored 1.6-6.2 (see epic c0f3037f decision log for the prototype numbers).
_BM25_MIN_SCORE = 1.2

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


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def _bm25_fallback(prompt: str, conn: sqlite3.Connection) -> Optional[str]:
    """Best-matching cached prompt key by BM25, or None if the corpus is empty or
    nothing clears `_BM25_MIN_SCORE`. Backup path only — called after exact-match misses.
    """
    rows = conn.execute("SELECT prompt FROM prompt_cache").fetchall()
    if not rows:
        return None
    corpus = [r["prompt"] for r in rows]
    bm25 = BM25Okapi([_tokenize(p) for p in corpus])
    scores = bm25.get_scores(_tokenize(prompt))
    best_idx = max(range(len(corpus)), key=lambda i: scores[i])
    if scores[best_idx] < _BM25_MIN_SCORE:
        return None
    return corpus[best_idx]


def _row_by_key(key: str, conn: sqlite3.Connection) -> Optional[sqlite3.Row]:
    return conn.execute(
        "SELECT prompt, cache, tags, commit_sha, source, last_updated, "
        "julianday('now') - julianday(last_updated) AS age_days "
        "FROM prompt_cache WHERE prompt = ?",
        (key,),
    ).fetchone()


def lookup_cache(prompt: str) -> Optional[dict]:
    """Look up a cached answer for `prompt`. Exact match (normalize + equality) first;
    if that misses, falls back to BM25 over all cached prompts to catch paraphrases.
    Returns the row as a dict, or None if neither matches.

    The returned dict includes `match_type` ("exact" or "fuzzy") — callers should say
    so explicitly on a "fuzzy" hit, since it's not the literal question that was asked.

    It also includes `source` ("code" or "websearch") which determines which staleness
    signal is meaningful:
      - source="code":      use `commits_behind` — how many commits have landed in this
                             repo since caching. `age_days` is a secondary signal only.
      - source="websearch": `commits_behind` is meaningless (external facts don't move
                             in lockstep with this repo's commits) — use `age_days` instead.
    """
    key = normalize_prompt(prompt)
    if not key:
        return None
    with _connect() as conn:
        row = _row_by_key(key, conn)
        match_type = "exact"
        if row is None:
            fallback_key = _bm25_fallback(key, conn)
            if fallback_key is None:
                return None
            row = _row_by_key(fallback_key, conn)
            match_type = "fuzzy"
    if not row:
        return None
    result = dict(row)
    result["match_type"] = match_type
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
    """Look up a cached answer for `prompt`. Tries exact match first; if that misses,
    falls back to BM25 over all cached prompts to catch paraphrases.

    Never serve the result silently — surface a confirmation to the user first, and
    say so explicitly on a `match_type="fuzzy"` hit (it's a paraphrase match, not the
    literal question asked — e.g. "The closest cached match is <the stored prompt>,
    N days old — want to see it?"). Check `source` to pick the right staleness signal:
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
