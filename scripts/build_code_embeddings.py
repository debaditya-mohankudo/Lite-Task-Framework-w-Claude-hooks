#!/usr/bin/env python3
"""Build TurboVec RAG embeddings for claude-hooks Python source.

Chunks each .py file by function/class body (AST-based, not line windows)
so each vector maps to a meaningful unit. Chunk text includes the full source
— docstrings and inline comments included — so embeddings capture both code
semantics and human explanation.

Output:
  .code_embeddings.npz   — vectors + metadata (gitignored)

Usage:
    uv run python scripts/build_code_embeddings.py
"""
from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".venv", "__pycache__", ".git", "node_modules", "tests"}
OUT_FILE = REPO_ROOT / ".code_embeddings.npz"
MODEL_NAME = "all-MiniLM-L6-v2"  # 80MB, 384-dim, fast


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


def _collect_files() -> list[Path]:
    files = []
    for p in sorted(REPO_ROOT.rglob("*.py")):
        if any(skip in p.parts for skip in SKIP_DIRS):
            continue
        files.append(p)
    return files


def _extract_chunks(path: Path) -> list[dict]:
    """Extract one chunk per top-level function or class (including methods)."""
    src = path.read_text(encoding="utf-8", errors="replace")
    lines = src.splitlines()
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return []

    rel = str(path.relative_to(REPO_ROOT))
    module = rel.replace("/", ".").removesuffix(".py")
    chunks = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chunk_lines = lines[node.lineno - 1: node.end_lineno]
            chunks.append({
                "module": module,
                "file": rel,
                "name": node.name,
                "kind": "function",
                "line": node.lineno,
                "text": "\n".join(chunk_lines),
            })
        elif isinstance(node, ast.ClassDef):
            # whole class as one chunk (includes all method docstrings)
            chunk_lines = lines[node.lineno - 1: node.end_lineno]
            chunks.append({
                "module": module,
                "file": rel,
                "name": node.name,
                "kind": "class",
                "line": node.lineno,
                "text": "\n".join(chunk_lines),
            })

    return chunks


def build_chunks() -> list[dict]:
    chunks = []
    for path in _collect_files():
        chunks.extend(_extract_chunks(path))
    return chunks


def embed(texts: list[str]) -> np.ndarray:
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL_NAME)
    return model.encode(texts, show_progress_bar=True, batch_size=64)


def main() -> None:
    print(f"Collecting chunks from {REPO_ROOT} ...")
    chunks = build_chunks()
    print(f"  {len(chunks)} chunks across {len(_collect_files())} files")

    texts = [c["text"] for c in chunks]
    print(f"Embedding with {MODEL_NAME} ...")
    vectors = embed(texts)  # shape: (N, 384)

    # strip text from metadata before saving (reconstruct from file+line at query time)
    meta = [{k: v for k, v in c.items() if k != "text"} for c in chunks]
    meta_json = json.dumps(meta)

    sha = _git_sha()
    np.savez_compressed(
        OUT_FILE,
        vectors=vectors,
        meta=np.array([meta_json]),
        commit=np.array([sha]),
    )
    print(f"Written {OUT_FILE.relative_to(REPO_ROOT)}")
    print(f"  shape: {vectors.shape}, commit: {sha[:12]}")


if __name__ == "__main__":
    main()
