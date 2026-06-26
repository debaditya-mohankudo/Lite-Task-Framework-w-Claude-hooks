"""Export claude-hooks memories to docs/memory/<slug>.json — one file per node.

Each file is a self-contained memory node. The `related` list contains slugs
that resolve to sibling files in the same directory.

Also writes docs/memory/_graph.json — adjacency list for fast traversal.

Usage:
    uv run python scripts/export_memory_graph.py
"""
import json
import sqlite3
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MEM_DIR   = _REPO_ROOT / "docs" / "memory"
_GRAPH_OUT = _MEM_DIR / "_graph.json"


def load_rows(db_path: Path | None = None) -> list[dict]:
    if db_path is None:
        db_path = Path.home() / ".claude" / "MEMORY.sqlite"
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """
        SELECT name, type, tags, body, files, docs, related, updated
        FROM memories
        WHERE domain = 'claude-hooks'
        ORDER BY name
        """
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def export(db_path: Path | None = None) -> None:
    _MEM_DIR.mkdir(parents=True, exist_ok=True)

    rows = load_rows(db_path)
    slugs = {r["name"] for r in rows}
    edges: list[dict] = []

    for row in rows:
        related = [s.strip() for s in (row["related"] or "").split(",") if s.strip()]
        node = {
            "name":    row["name"],
            "type":    row["type"],
            "tags":    row["tags"] or "",
            "body":    row["body"] or "",
            "files":   (row["files"] or "").split(",") if row["files"] else [],
            "docs":    (row["docs"] or "").split(",") if row["docs"] else [],
            "related": related,
            "updated": row["updated"],
        }
        path = _MEM_DIR / f"{row['name']}.json"
        path.write_text(json.dumps(node, indent=2, ensure_ascii=False) + "\n")

        for target in related:
            edges.append({"source": row["name"], "target": target, "target_exists": target in slugs})

    # Adjacency list for graph traversal
    adjacency: dict[str, list[str]] = {r["name"]: [] for r in rows}
    for e in edges:
        if e["target_exists"]:
            adjacency[e["source"]].append(e["target"])

    graph = {
        "domain":     "claude-hooks",
        "node_count": len(rows),
        "edge_count": len([e for e in edges if e["target_exists"]]),
        "adjacency":  adjacency,
    }
    _GRAPH_OUT.write_text(json.dumps(graph, indent=2) + "\n")

    print(f"✓ {graph['node_count']} nodes, {graph['edge_count']} edges")
    print(f"  nodes → {_MEM_DIR.relative_to(_REPO_ROOT)}/<slug>.json")
    print(f"  graph → {_GRAPH_OUT.relative_to(_REPO_ROOT)}")


def load_graph() -> dict:
    """Load adjacency list from _graph.json for traversal."""
    return json.loads(_GRAPH_OUT.read_text())


def neighbours(slug: str, hops: int = 1) -> set[str]:
    """Return all slugs within N hops of slug."""
    adj = load_graph()["adjacency"]
    visited, frontier = {slug}, {slug}
    for _ in range(hops):
        next_frontier = set()
        for s in frontier:
            for t in adj.get(s, []):
                if t not in visited:
                    next_frontier.add(t)
        visited |= next_frontier
        frontier = next_frontier
    return visited - {slug}


def most_connected(top_n: int = 10) -> list[tuple[str, int]]:
    """Return top-N nodes by out-degree (number of related links)."""
    adj = load_graph()["adjacency"]
    ranked = sorted(adj.items(), key=lambda x: -len(x[1]))
    return [(slug, len(links)) for slug, links in ranked[:top_n]]


if __name__ == "__main__":
    export()
