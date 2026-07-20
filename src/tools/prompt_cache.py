"""Custom prompt-caching mechanism — epic c0f3037f.

Not Anthropic's cache_control (that's an exact byte-prefix match on the raw
request). This is a homegrown cache keyed by *normalized* prompt text, meant
to answer recurring "how does X work?" questions without re-spending
reasoning tokens. `lookup_cache` (the CacheCheckNode hot path, runs on every
UserPromptSubmit) stays deliberately global — not scoped to any repo/cwd —
so a hit from one project can serve a repeat question asked from another.
`domain` (task:91dad030) is populated at store time from an explicit `cwd`
and used only to scope `list_cache`/`search_cache` browsing — it does NOT
change lookup_cache's matching, to avoid silently narrowing the hot-path
reuse this module is validated for. No skill currently calls
prompt_cache__lookup/__store directly (e.g. happiest-minds-tracker's
summarization step does not); today the only caller is CacheCheckNode,
which checks every UserPromptSubmit's raw prompt text.

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
    if "domain" not in cols:
        conn.execute("ALTER TABLE prompt_cache ADD COLUMN domain TEXT DEFAULT ''")
    conn.commit()
    return conn


def _domain_from_cwd(cwd: str) -> Optional[str]:
    """Match cwd path components against cwd_domain_map from config (same map/logic
    as src.tools.tasks._domain_from_cwd — kept local so this module has no dependency
    on the task system, just on the shared config).
    """
    if not cwd:
        return None
    try:
        from src.config import config as _src_cfg
        cwd_map = _src_cfg.cwd_domain_map
        for part in reversed(Path(cwd).resolve().parts):
            if part in cwd_map:
                return cwd_map[part]
    except Exception:
        return None
    return None


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
        "SELECT prompt, cache, tags, commit_sha, source, domain, last_updated, "
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


def store_cache(prompt: str, cache: str, tags: str = "", source: str = "code", domain: str = "", cwd: str = "") -> dict:
    """Normalize `prompt` and upsert a cache row. Returns the stored row's key fields.

    Args:
        source: "code" (default) — tags the entry with the current repo's HEAD commit
                (the "tip of commit" at cache time) so later lookups can tell how many
                commits have landed since. Use "websearch" for answers sourced from
                WebSearch/WebFetch, where commit-based staleness doesn't apply —
                commit_sha is left empty and callers should rely on age_days instead.
        domain: Explicit domain tag (e.g. "seniordevagent", "claude-hooks"). Overrides
                domain inferred from `cwd`. Scopes list_cache/search_cache browsing only —
                does not affect lookup_cache, which stays intentionally global.
        cwd:    Caller's working directory, used to infer `domain` via cwd_domain_map
                (same map as tasks__create's cwd->domain detection) when `domain` isn't
                given explicitly. Ignored if `domain` is set.
    """
    key = normalize_prompt(prompt)
    if not key:
        return {"error": "prompt normalizes to empty string — nothing to cache"}
    commit_sha = _current_commit_sha() if source == "code" else ""
    domain = domain or _domain_from_cwd(cwd) or ""
    with _connect() as conn:
        conn.execute(
            "INSERT INTO prompt_cache (prompt, cache, tags, commit_sha, source, domain, last_updated) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now')) "
            "ON CONFLICT(prompt) DO UPDATE SET "
            "cache=excluded.cache, tags=excluded.tags, commit_sha=excluded.commit_sha, "
            "source=excluded.source, domain=excluded.domain, last_updated=excluded.last_updated",
            (key, cache, tags, commit_sha, source, domain),
        )
        conn.commit()
    return {"prompt": key, "tags": tags, "commit_sha": commit_sha, "source": source, "domain": domain}


def delete_cache(prompt: str) -> dict:
    """Normalize `prompt` and delete its cache row, if any. Returns {"deleted": bool}."""
    key = normalize_prompt(prompt)
    if not key:
        return {"deleted": False}
    with _connect() as conn:
        cur = conn.execute("DELETE FROM prompt_cache WHERE prompt = ?", (key,))
        conn.commit()
    return {"deleted": cur.rowcount > 0}


_LIST_COLUMNS = "prompt, tags, source, commit_sha, domain, last_updated"


def list_cache(source: str = "", tags: str = "", domain: str = "") -> list[dict]:
    """List all cache entries (metadata only, no `cache` body — use lookup_cache for that).

    Args:
        source: Optional filter ("code" or "websearch").
        tags:   Optional substring filter over the tags column.
        domain: Optional exact filter over the domain column (e.g. "seniordevagent").
                Entries stored before domain scoping was added have domain="" and are
                excluded by any non-empty domain filter.
    """
    sql = f"SELECT {_LIST_COLUMNS} FROM prompt_cache WHERE 1=1"
    params: list = []
    if source:
        sql += " AND source = ?"
        params.append(source)
    if tags:
        sql += " AND tags LIKE ?"
        params.append(f"%{tags}%")
    if domain:
        sql += " AND domain = ?"
        params.append(domain)
    sql += " ORDER BY last_updated DESC, rowid DESC"
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def search_cache(query: str, domain: str = "") -> list[dict]:
    """BM25-rank all cached prompts against `query`, returning matches above `_BM25_MIN_SCORE`.

    Unlike lookup_cache (single best hit, used for the "have we answered this exact
    question before" check), this surfaces every plausible match so a human/agent can
    browse — e.g. "what have we cached about task-framework".

    Args:
        domain: Optional exact filter over the domain column, applied before BM25
                ranking — narrows the corpus to one project's entries.
    """
    sql = f"SELECT {_LIST_COLUMNS} FROM prompt_cache WHERE 1=1"
    params: list = []
    if domain:
        sql += " AND domain = ?"
        params.append(domain)
    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()
        if not rows:
            return []
        corpus = [r["prompt"] for r in rows]
        bm25 = BM25Okapi([_tokenize(p) for p in corpus])
        scores = bm25.get_scores(_tokenize(query))
        ranked = sorted(
            (i for i in range(len(corpus)) if scores[i] >= _BM25_MIN_SCORE),
            key=lambda i: scores[i],
            reverse=True,
        )
        return [dict(rows[i], score=round(float(scores[i]), 2)) for i in ranked]


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


def handle_store(prompt: str, cache: str, tags: str = "", source: str = "code", domain: str = "", cwd: str = "") -> dict:
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
        domain: Explicit domain tag (e.g. "seniordevagent", "claude-hooks"). Overrides
                domain inferred from `cwd`. Scopes list/search browsing only — lookup
                stays global across domains.
        cwd:    Current working directory, used to infer `domain` (via the same
                cwd_domain_map tasks__create uses) when `domain` isn't given explicitly.
    """
    return store_cache(prompt, cache, tags, source, domain, cwd)


def handle_list(source: str = "", tags: str = "", domain: str = "") -> dict:
    """List all cache entries (metadata only — prompt/tags/source/commit_sha/domain/
    last_updated, no `cache` answer body). Use `prompt_cache__lookup` to fetch a
    specific entry's answer.

    Args:
        source: Optional filter ("code" or "websearch").
        tags:   Optional substring filter over the tags column.
        domain: Optional exact filter over the domain column (e.g. "seniordevagent") —
                use this to browse one project's entries without cross-repo noise.
    """
    rows = list_cache(source, tags, domain)
    return {"count": len(rows), "results": rows}


def handle_search(query: str, domain: str = "") -> dict:
    """Search cached prompts by keyword (BM25), returning every plausible match — not
    just the single best hit. Use this to browse "what have we cached about X" rather
    than checking whether one specific question was already answered (use
    `prompt_cache__lookup` for that).

    Args:
        query:  Keyword(s) to search for across cached prompt text.
        domain: Optional exact filter over the domain column, applied before ranking.
    """
    rows = search_cache(query, domain)
    return {"count": len(rows), "results": rows}


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
