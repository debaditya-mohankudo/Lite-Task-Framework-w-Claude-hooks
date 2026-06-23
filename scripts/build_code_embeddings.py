#!/usr/bin/env python3
"""Build TurboVec RAG embeddings for claude-hooks docs.

Chunks each .md file in docs/ by ## section. Each chunk maps to a stable
uint64 ID derived from sha256(file+name) so incremental upserts work without
a full rebuild.

Output:
  .code_embeddings.tvim      — TurboVec IdMapIndex (gitignored)
  .code_embeddings.meta.json — chunk metadata sidecar (gitignored)

Usage:
    uv run python scripts/build_code_embeddings.py              # full rebuild
    uv run python scripts/build_code_embeddings.py --files docs/arch/graph_pipeline.md
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

REPO_ROOT  = Path(__file__).resolve().parent.parent
DOCS_DIRS  = ["docs"]
TVIM_FILE  = REPO_ROOT / ".code_embeddings.tvim"
META_FILE  = REPO_ROOT / ".code_embeddings.meta.json"
MODEL_NAME = "nomic-embed-text"  # Ollama local model, 768-dim


# ---------------------------------------------------------------------------
# Stable chunk IDs
# ---------------------------------------------------------------------------

def _chunk_id(file: str, name: str) -> int:
    """Deterministic uint64 from (file, name) — stable across rebuilds."""
    digest = hashlib.sha256(f"{file}::{name}".encode()).digest()
    return int.from_bytes(digest[:8], "little") & 0x7FFF_FFFF_FFFF_FFFF


# ---------------------------------------------------------------------------
# Index persistence
# ---------------------------------------------------------------------------

def _load_index():
    """Return (IdMapIndex, meta_dict) or (None, {}) if not found."""
    import turbovec
    if not TVIM_FILE.exists() or not META_FILE.exists():
        return None, {}
    try:
        index = turbovec.IdMapIndex.load(str(TVIM_FILE))
        meta  = json.loads(META_FILE.read_text())
        return index, meta
    except Exception:
        return None, {}


def _save_index(index, meta: dict) -> None:
    """Atomic write: .tvim.tmp → .tvim, .meta.json.tmp → .meta.json."""
    tmp_tvim = TVIM_FILE.with_suffix(".tvim.tmp")
    tmp_meta = META_FILE.with_suffix(".json.tmp")
    index.write(str(tmp_tvim))
    tmp_meta.write_text(json.dumps(meta))
    tmp_tvim.rename(TVIM_FILE)
    tmp_meta.rename(META_FILE)


# ---------------------------------------------------------------------------
# Git SHA
# ---------------------------------------------------------------------------

def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, text=True
        ).strip()
    except Exception:
        return "unknown"


# ---------------------------------------------------------------------------
# Chunk extraction
# ---------------------------------------------------------------------------

def _collect_docs() -> list[Path]:
    docs = []
    for d in DOCS_DIRS:
        for p in sorted((REPO_ROOT / d).rglob("*.md")):
            docs.append(p)
    return docs


def _extract_md_chunks(path: Path) -> list[dict]:
    import re
    src    = path.read_text(encoding="utf-8", errors="replace")
    rel    = str(path.relative_to(REPO_ROOT))
    module = rel.replace("/", ".").removesuffix(".md")

    # Strip fenced code blocks so ## inside examples don't create phantom sections
    src_no_fences = re.sub(r"(?ms)^```.*?^```", lambda m: "\n" * m.group().count("\n"), src)
    sections    = re.split(r"(?m)^(?=## )", src_no_fences)
    chunks      = []
    line_cursor = 1
    for section in sections:
        if not section.strip():
            line_cursor += section.count("\n")
            continue
        heading_match = re.match(r"^##+ (.+)", section)
        name = heading_match.group(1).strip() if heading_match else module
        chunks.append({
            "id":     _chunk_id(rel, name),
            "module": module, "file": rel,
            "name":   name, "kind": "section", "line": line_cursor,
            "text":   section.rstrip(),
        })
        line_cursor += section.count("\n")
    return chunks


def build_chunks() -> list[dict]:
    chunks = []
    for path in _collect_docs():
        chunks.extend(_extract_md_chunks(path))
    return chunks


def chunks_for_files(rel_paths: list[str]) -> list[dict]:
    """Return chunks for a specific subset of repo-relative file paths."""
    chunks = []
    for rel in rel_paths:
        path = REPO_ROOT / rel
        if not path.exists():
            print(f"  [skip] not found: {rel}")
            continue
        if path.suffix == ".md":
            chunks.extend(_extract_md_chunks(path))
        else:
            print(f"  [skip] not a .md file: {rel}")
    return chunks


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def _embed(texts: list[str]) -> np.ndarray:
    from llama_index.embeddings.ollama import OllamaEmbedding
    model = OllamaEmbedding(model_name=MODEL_NAME)
    vecs = model.get_text_embedding_batch(texts, show_progress=True)
    return np.array(vecs, dtype=np.float32)


# ---------------------------------------------------------------------------
# Full rebuild
# ---------------------------------------------------------------------------

def _full_build() -> None:
    import turbovec

    chunks = build_chunks()
    print(f"  {len(chunks)} chunks across {len(_collect_docs())} docs")

    texts   = [c["text"] for c in chunks]
    vectors = _embed(texts)

    ids  = np.array([c["id"] for c in chunks], dtype=np.uint64)
    meta = {str(c["id"]): {k: v for k, v in c.items() if k not in ("text", "id")} for c in chunks}
    # store commit in meta under special key
    meta["__commit__"] = _git_sha()

    index = turbovec.IdMapIndex()
    index.add_with_ids(vectors, ids)
    index.prepare()

    _save_index(index, meta)

    sha = meta["__commit__"]
    print(f"Written {TVIM_FILE.relative_to(REPO_ROOT)}")
    print(f"  shape: {vectors.shape}, commit: {sha[:12]}")


# ---------------------------------------------------------------------------
# Incremental upsert
# ---------------------------------------------------------------------------

def _incremental_upsert(rel_paths: list[str]) -> None:
    import turbovec

    index, meta = _load_index()
    if index is None:
        print("No existing index — falling back to full rebuild.")
        _full_build()
        return

    new_chunks = chunks_for_files(rel_paths)
    if not new_chunks:
        print("No chunks extracted from given files.")
        return

    # Determine which IDs are already in index (by file overlap)
    files_set  = {c["file"] for c in new_chunks}
    old_ids    = [
        int(cid) for cid, info in meta.items()
        if cid != "__commit__" and info.get("file") in files_set
    ]

    # Remove old vectors
    for old_id in old_ids:
        index.remove(np.uint64(old_id))
        meta.pop(str(old_id), None)

    texts   = [c["text"] for c in new_chunks]
    vectors = _embed(texts)
    ids     = np.array([c["id"] for c in new_chunks], dtype=np.uint64)

    index.add_with_ids(vectors, ids)
    index.prepare()

    for c in new_chunks:
        meta[str(c["id"])] = {k: v for k, v in c.items() if k not in ("text", "id")}

    meta["__commit__"] = _git_sha()
    _save_index(index, meta)

    sha = meta["__commit__"]
    print(f"Upserted {len(new_chunks)} chunks (removed {len(old_ids)} old) — commit: {sha[:12]}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--files", nargs="+", metavar="PATH",
        help="Repo-relative .md file paths to upsert (incremental). Omit for full rebuild.",
    )
    args = parser.parse_args()

    if args.files:
        print(f"Incremental upsert for: {args.files}")
        _incremental_upsert(args.files)
    else:
        print(f"Scanning {REPO_ROOT / 'docs'} ...")
        _full_build()


if __name__ == "__main__":
    main()
