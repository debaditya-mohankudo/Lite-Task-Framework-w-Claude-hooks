#!/usr/bin/env python3
"""
Weekly refresh of mcp_tool_hints keywords.

## How it fits into the pipeline

1. PostToolUse hook (tool_usage_logger_lc.py) — on every MCP tool call, appends the
   current prompt text to `recent_prompts` (last 10) and accumulates raw keywords
   from the prompt into the `keywords` column via simple set-union.

2. This script (refresh_tool_hints.py) — runs weekly via cron, reads all
   `recent_prompts` from mcp_tool_hints, re-derives `keywords` using TF-IDF across
   the full tool corpus, and overwrites the `keywords` column. This replaces the
   raw accumulated keywords with cleaner, ranked signal.

3. ToolHintsRetriever (langchain_learning/tool_hints_retriever.py) — on every
   UserPromptSubmit, uses the `keywords` column for BM25 retrieval to surface the
   most relevant MCP tools for the current prompt, injected as `## Suggested tools`
   in the system prompt.

TF-IDF ensures tools used in diverse contexts get broad keywords, while tools used
in narrow contexts (e.g. panchang__date) get precise, topic-specific keywords.

Cron (Sunday 3am):
    0 3 * * 0  cd ~/workspace/claude-hooks && /Users/debaditya/.local/bin/uv run python hooks/refresh_tool_hints.py >> ~/Library/Logs/refresh_tool_hints.log 2>&1
"""
import json
import math
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path

_PROJECT_ROOT = Path.home() / "workspace/claude-hooks"
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from src.config import config as _cfg
from sqlite_log_handler import setup

log = setup("refresh_tool_hints")

_TOOL_HINTS_DB = _cfg.tool_hints_db

def _load_stopwords(db_path: Path) -> set[str]:
    with sqlite3.connect(str(db_path)) as conn:
        rows = conn.execute("SELECT word FROM stopwords").fetchall()
    return {r[0] for r in rows}


_STOPWORDS: set[str] = set()  # populated in main() from DB


_XML_TAG_RE = re.compile(r"<[^>]+>.*?</[^>]+>", re.DOTALL)
_TAG_RE     = re.compile(r"<[^>]+>")


def _clean_prompt(text: str) -> str:
    text = _XML_TAG_RE.sub(" ", text)  # strip <ide_opened_file>...</ide_opened_file> etc.
    text = _TAG_RE.sub(" ", text)      # strip any remaining open tags
    return text


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z]{3,}", _clean_prompt(text).lower())
    return [t for t in tokens if t not in _STOPWORDS]


def _tfidf_keywords(tool_prompts: dict[str, list[str]], top_n: int = 15) -> dict[str, str]:
    """
    Compute TF-IDF keywords per tool from their recent_prompts.
    tool_prompts: {tool_name: [prompt, prompt, ...]}
    Returns: {tool_name: "kw1,kw2,..."}
    """
    # Build corpus: one doc per tool (all prompts concatenated)
    docs: dict[str, list[str]] = {
        tool: _tokenize(" ".join(prompts))
        for tool, prompts in tool_prompts.items()
        if prompts
    }

    n_docs = len(docs)
    if n_docs == 0:
        return {}

    # IDF: how many docs contain each term
    df: Counter = Counter()
    for tokens in docs.values():
        df.update(set(tokens))

    idf: dict[str, float] = {
        term: math.log((n_docs + 1) / (count + 1)) + 1.0
        for term, count in df.items()
    }

    result: dict[str, str] = {}
    for tool, tokens in docs.items():
        if not tokens:
            result[tool] = ""
            continue
        tf = Counter(tokens)
        total = len(tokens)
        scores = {
            term: (count / total) * idf.get(term, 1.0)
            for term, count in tf.items()
        }
        top = sorted(scores, key=lambda t: scores[t], reverse=True)[:top_n]
        result[tool] = ",".join(sorted(top))

    return result


def _load_tool_prompts(conn: sqlite3.Connection) -> dict[str, list[str]]:
    rows = conn.execute(
        "SELECT tool_name, recent_prompts FROM mcp_tool_hints"
    ).fetchall()
    out: dict[str, list[str]] = {}
    for tool_name, recent_json in rows:
        try:
            prompts = json.loads(recent_json or "[]")
            out[tool_name] = [p for p in prompts if isinstance(p, str) and p.strip()]
        except Exception:
            out[tool_name] = []
    return out


def _update_keywords(conn: sqlite3.Connection, keywords_map: dict[str, str]) -> int:
    updated = 0
    for tool_name, kws in keywords_map.items():
        conn.execute(
            "UPDATE mcp_tool_hints SET keywords = ? WHERE tool_name = ?",
            (kws, tool_name),
        )
        updated += 1
    conn.commit()
    return updated


def main() -> None:
    if not _TOOL_HINTS_DB.exists():
        log.error("tool_hints DB not found: %s", _TOOL_HINTS_DB)
        sys.exit(1)

    global _STOPWORDS
    _STOPWORDS = _load_stopwords(_TOOL_HINTS_DB)
    log.info("refresh_tool_hints: starting")

    with sqlite3.connect(str(_TOOL_HINTS_DB)) as conn:
        tool_prompts = _load_tool_prompts(conn)
        log.info("loaded %d tools", len(tool_prompts))

        keywords_map = _tfidf_keywords(tool_prompts)
        n = _update_keywords(conn, keywords_map)

    log.info("refresh_tool_hints: updated keywords for %d tools", n)
    for tool, kws in sorted(keywords_map.items()):
        log.debug("  %s → %s", tool, kws or "(empty)")


if __name__ == "__main__":
    main()
