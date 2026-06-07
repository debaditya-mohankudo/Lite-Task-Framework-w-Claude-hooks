#!/usr/bin/env python3
"""Query .code_embeddings.npz for top-k chunks matching a natural language query.

Usage:
    uv run python scripts/query_code_embeddings.py "how does compaction work" --k 5
    uv run python scripts/query_code_embeddings.py "domain scoring" --k 3
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
EMB_FILE = REPO_ROOT / ".code_embeddings.npz"
MODEL_NAME = "all-MiniLM-L6-v2"
SNIPPET_LINES = 15  # lines of source to show per result


def _load_index() -> tuple[np.ndarray, list[dict], str]:
    if not EMB_FILE.exists():
        raise FileNotFoundError(
            f"{EMB_FILE.name} not found — run: uv run python scripts/build_code_embeddings.py"
        )
    data = np.load(EMB_FILE, allow_pickle=False)
    vectors = data["vectors"]
    meta = json.loads(data["meta"][0])
    commit = data["commit"][0]
    return vectors, meta, commit


def _embed_query(text: str) -> np.ndarray:
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL_NAME)
    return model.encode([text])  # shape: (1, 384)


def _cosine_search(query_vec: np.ndarray, vectors: np.ndarray, k: int) -> list[int]:
    """Return top-k indices by cosine similarity."""
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms = np.where(norms == 0, 1, norms)
    normed = vectors / norms
    q = query_vec / (np.linalg.norm(query_vec) or 1)
    scores = normed @ q.T  # (N, 1)
    scores = scores.flatten()
    return list(np.argsort(scores)[::-1][:k]), scores


def _read_snippet(file: str, line: int) -> str:
    path = REPO_ROOT / file
    if not path.exists():
        return "(source not found)"
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    start = max(0, line - 1)
    end = min(len(lines), start + SNIPPET_LINES)
    return "\n".join(lines[start:end])


def query(text: str, k: int = 5) -> list[dict]:
    vectors, meta, commit = _load_index()
    print(f"Index: {len(meta)} chunks, commit {commit[:12]}")

    query_vec = _embed_query(text)
    indices, scores = _cosine_search(query_vec, vectors, k)

    results = []
    for idx in indices:
        m = meta[idx]
        results.append({
            "score": float(scores[idx]),
            "module": m["module"],
            "file": m["file"],
            "name": m["name"],
            "kind": m["kind"],
            "line": m["line"],
            "snippet": _read_snippet(m["file"], m["line"]),
        })
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", help="Natural language query")
    parser.add_argument("--k", type=int, default=5, help="Number of results")
    args = parser.parse_args()

    results = query(args.query, k=args.k)
    for i, r in enumerate(results, 1):
        print(f"\n{'='*60}")
        print(f"#{i} [{r['score']:.3f}] {r['kind']} {r['name']}")
        print(f"    {r['file']}:{r['line']}")
        print(f"{'─'*60}")
        print(r["snippet"])


if __name__ == "__main__":
    main()
