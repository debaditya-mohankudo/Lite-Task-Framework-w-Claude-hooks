#!/usr/bin/env python3
"""Build TurboVec RAG embeddings for claude-hooks Python source.

Chunks each .py file by function/class body (AST-based) and each .md file in
docs/ by ## section. Each chunk maps to a stable uint64 ID derived from
hash(file+name) so incremental upserts work without a full rebuild.

Output:
  .code_embeddings.tvim      — TurboVec IdMapIndex (gitignored)
  .code_embeddings.meta.json — chunk metadata sidecar (gitignored)

Usage:
    uv run python scripts/build_code_embeddings.py              # full rebuild
    uv run python scripts/build_code_embeddings.py --files langchain_learning/nodes/load_turn.py
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

REPO_ROOT  = Path(__file__).resolve().parent.parent
SKIP_DIRS  = {".venv", "__pycache__", ".git", "node_modules", "tests"}
DOCS_DIRS  = ["docs"]
TVIM_FILE  = REPO_ROOT / ".code_embeddings.tvim"
META_FILE  = REPO_ROOT / ".code_embeddings.meta.json"
MODEL_NAME = "all-MiniLM-L6-v2"


# ---------------------------------------------------------------------------
# Stable chunk IDs
# ---------------------------------------------------------------------------

def _chunk_id(file: str, name: str) -> int:
    """Deterministic uint64 from (file, name) — stable across rebuilds."""
    return hash(f"{file}::{name}") & 0xFFFF_FFFF_FFFF_FFFF


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

def _collect_files() -> list[Path]:
    files = []
    for p in sorted(REPO_ROOT.rglob("*.py")):
        if any(skip in p.parts for skip in SKIP_DIRS):
            continue
        files.append(p)
    return files


def _collect_docs() -> list[Path]:
    docs = []
    for d in DOCS_DIRS:
        for p in sorted((REPO_ROOT / d).rglob("*.md")):
            docs.append(p)
    return docs


def _extract_chunks(path: Path) -> list[dict]:
    src   = path.read_text(encoding="utf-8", errors="replace")
    lines = src.splitlines()
    try:
        tree = ast.parse(src, filename=str(path))
    except SyntaxError:
        return []

    rel    = str(path.relative_to(REPO_ROOT))
    module = rel.replace("/", ".").removesuffix(".py")
    chunks = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            chunks.append({
                "id":     _chunk_id(rel, node.name),
                "module": module, "file": rel,
                "name":   node.name, "kind": "function", "line": node.lineno,
                "text":   "\n".join(lines[node.lineno - 1: node.end_lineno]),
            })
        elif isinstance(node, ast.ClassDef):
            chunks.append({
                "id":     _chunk_id(rel, node.name),
                "module": module, "file": rel,
                "name":   node.name, "kind": "class", "line": node.lineno,
                "text":   "\n".join(lines[node.lineno - 1: node.end_lineno]),
            })

    return chunks


def _extract_md_chunks(path: Path) -> list[dict]:
    import re
    src    = path.read_text(encoding="utf-8", errors="replace")
    rel    = str(path.relative_to(REPO_ROOT))
    module = rel.replace("/", ".").removesuffix(".md")

    sections    = re.split(r"(?m)^(?=## )", src)
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
    for path in _collect_files():
        chunks.extend(_extract_chunks(path))
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
        if path.suffix == ".py":
            chunks.extend(_extract_chunks(path))
        elif path.suffix == ".md":
            chunks.extend(_extract_md_chunks(path))
    return chunks


# ---------------------------------------------------------------------------
# Tag / topology boosting (build-time only)
# ---------------------------------------------------------------------------

def _node_name_to_key(class_name: str) -> str:
    import re
    name = class_name.removesuffix("Node")
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _boost_tags(text: str, extra_tags: str = "", repeat: int = 3) -> str:
    import re
    m    = re.search(r"Tags:\s*(.+)", text)
    base = m.group(1).strip() if m else ""
    combined = ", ".join(filter(None, [base, extra_tags]))
    if not combined:
        return text
    tag_line = "Tags: " + combined
    return "\n".join([tag_line] * repeat) + "\n" + text


def _make_texts(chunks: list[dict], topology: dict) -> list[str]:
    def _extra(chunk: dict) -> str:
        if chunk.get("kind") != "class":
            return ""
        key  = _node_name_to_key(chunk.get("name", ""))
        info = topology.get(key)
        return f"chain:{info['chain']}, chain-position:{info['position']}" if info else ""

    return [_boost_tags(c["text"], _extra(c)) for c in chunks]


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------

def _embed(texts: list[str]) -> np.ndarray:
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(MODEL_NAME)
    return model.encode(texts, show_progress_bar=True, batch_size=64)


# ---------------------------------------------------------------------------
# Full rebuild
# ---------------------------------------------------------------------------

def _full_build(topology: dict) -> None:
    import turbovec

    chunks = build_chunks()
    print(f"  {len(chunks)} chunks across {len(_collect_files())} files")

    texts   = _make_texts(chunks, topology)
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

def _incremental_upsert(rel_paths: list[str], topology: dict) -> None:
    import turbovec

    index, meta = _load_index()
    if index is None:
        print("No existing index — falling back to full rebuild.")
        _full_build(topology)
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

    texts   = _make_texts(new_chunks, topology)
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
    sys.path.insert(0, str(REPO_ROOT))
    from scripts.graph_topology import get_node_topology
    topology = get_node_topology()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--files", nargs="+", metavar="PATH",
        help="Repo-relative file paths to upsert (incremental). Omit for full rebuild.",
    )
    args = parser.parse_args()

    if args.files:
        print(f"Incremental upsert for: {args.files}")
        _incremental_upsert(args.files, topology)
    else:
        print(f"Scanning {REPO_ROOT} ...")
        _full_build(topology)


if __name__ == "__main__":
    main()
