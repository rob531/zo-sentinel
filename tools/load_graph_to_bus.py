#!/usr/bin/env python3
"""
load_graph_to_bus.py -- Phase 2: mirror graphify-out/graph.json into DuckDB
`code_nodes` / `code_edges` over the write_service :8772 bus.

This is the structural-context half of the graph-native feedback loop: graphifyy
stays the build-time tree-sitter extractor, but the graph is PERSISTED in DuckDB
so the architect can query neighborhoods/paths AND JOIN them to live pass/fail
tables (agent_runs, inference_log, corrections) -- which a standalone graph MCP
cannot do. See DESIGN_graph_native_feedback.md.

Constraints honoured:
  - NO direct `duckdb` import. DDL + writes + reads all go through the single
    serialized writer at 127.0.0.1:8772 (/execute, /write, /query).
  - Rows stamped with the graph's `built_at_commit` (baked into graph.json), so
    a rebuild is append + `WHERE built_at_commit = (SELECT MAX(...))`.
  - Idempotent: re-running for the same commit DELETEs that commit's rows first,
    then reloads -- no duplicates. --purge-old drops other commits' rows.
  - Batched (~1k rows/POST) so the write_queue doesn't choke (the 651-lock
    lesson: never dump a herd of writes at the single writer at once).

graph.json (node-link, networkx): nodes{id,label,norm_label,file_type,
source_file,source_location,community,_origin}; links{source,target,relation,
weight,confidence,confidence_score,source_file,source_location}.

Run on the box (after the graph exists -- `uv run tools/index_graph.py`):
  python3 tools/load_graph_to_bus.py --dry-run    # parse + count, no writes
  python3 tools/load_graph_to_bus.py              # seed the bus
  python3 tools/load_graph_to_bus.py --purge-old  # seed + drop stale commits
"""
from __future__ import annotations

import argparse
import json
import pathlib
import subprocess
import sys
from collections import Counter

WS = "http://127.0.0.1:8772"
ROOT = pathlib.Path(__file__).resolve().parent.parent
GRAPH = ROOT / "graphify-out" / "graph.json"

# write_service inserts via INSERT OR IGNORE (ON CONFLICT DO NOTHING), which
# DuckDB rejects unless the table has a PRIMARY KEY/UNIQUE constraint -- so these
# tables MUST declare one. Verified unique on the real graph: node id is unique;
# (src,dst,relation) is unique. PK columns are all non-null.
DDL_NODES = (
    "CREATE TABLE IF NOT EXISTS code_nodes ("
    "id VARCHAR, label VARCHAR, norm_label VARCHAR, file_type VARCHAR, "
    "source_file VARCHAR, source_location VARCHAR, community INTEGER, "
    "built_at_commit VARCHAR, PRIMARY KEY (id, built_at_commit))"
)
DDL_EDGES = (
    "CREATE TABLE IF NOT EXISTS code_edges ("
    "src VARCHAR, dst VARCHAR, relation VARCHAR, weight DOUBLE, "
    "confidence VARCHAR, confidence_score DOUBLE, source_file VARCHAR, "
    "source_location VARCHAR, built_at_commit VARCHAR, "
    "PRIMARY KEY (src, dst, relation, built_at_commit))"
)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _i(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _s(v):
    return None if v is None else str(v)


def git_head() -> str:
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=str(ROOT),
                             capture_output=True, text=True, timeout=10).stdout.strip()
        return out or "unknown"
    except Exception:
        return "unknown"


def load_graph():
    data = json.loads(GRAPH.read_text(encoding="utf-8"))
    nodes = data.get("nodes", [])
    links = data.get("links", data.get("edges", []))
    # Prefer the commit the graph was built at; fall back to current HEAD.
    commit = data.get("built_at_commit") or git_head()
    return nodes, links, commit


def node_row(n, commit):
    return {
        "id": _s(n.get("id")),
        "label": _s(n.get("label")),
        "norm_label": _s(n.get("norm_label") or (n.get("label") or "").lower()),
        "file_type": _s(n.get("file_type")),
        "source_file": _s(n.get("source_file")),
        "source_location": _s(n.get("source_location")),
        "community": _i(n.get("community")),
        "built_at_commit": commit,
    }


def edge_row(e, commit):
    return {
        "src": _s(e.get("source")),
        "dst": _s(e.get("target")),
        "relation": _s(e.get("relation")),
        "weight": _f(e.get("weight")),
        "confidence": _s(e.get("confidence")),
        "confidence_score": _f(e.get("confidence_score")),
        "source_file": _s(e.get("source_file")),
        "source_location": _s(e.get("source_location")),
        "built_at_commit": commit,
    }


# --- bus helpers (lazy requests import so --dry-run needs no deps) ------------
def _post(path, payload, timeout):
    import requests
    r = requests.post(f"{WS}{path}", json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def execute(sql, params=None):
    return _post("/execute", {"sql": sql, "params": params or [],
                              "agent_id": "graph_loader", "wait": True}, 60)


def write_batch(table, rows):
    return _post("/write", {"table": table, "rows": rows,
                            "agent_id": "graph_loader", "wait": True}, 120)


def query(sql, params=None):
    return _post("/query", {"sql": sql, "params": params or []}, 30)


def push(table, rows, batch):
    for i in range(0, len(rows), batch):
        write_batch(table, rows[i:i + batch])
        print(f"  {table}: {min(i + batch, len(rows))}/{len(rows)}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Load graphify graph.json into DuckDB via :8772")
    ap.add_argument("--dry-run", action="store_true", help="parse + count only; no writes")
    ap.add_argument("--batch", type=int, default=1000, help="rows per /write POST (default 1000)")
    ap.add_argument("--purge-old", action="store_true", help="after load, delete rows from other commits")
    args = ap.parse_args(argv)

    if not GRAPH.is_file():
        print(f"ERROR: {GRAPH} not found -- build it first: uv run tools/index_graph.py",
              file=sys.stderr)
        return 2

    nodes, links, commit = load_graph()
    node_rows = [node_row(n, commit) for n in nodes]
    edge_rows = [edge_row(e, commit) for e in links]
    print(f"graph.json: {len(node_rows)} nodes, {len(edge_rows)} edges; built_at_commit={commit}")

    if args.dry_run:
        rels = Counter(e["relation"] for e in edge_rows)
        print("relations:", dict(rels.most_common(12)))
        print("sample node:", json.dumps(node_rows[0]) if node_rows else "none")
        print("sample edge:", json.dumps(edge_rows[0]) if edge_rows else "none")
        nullsrc = sum(1 for e in edge_rows if not e["src"] or not e["dst"])
        print(f"edges with empty src/dst: {nullsrc}")
        print("DRY-RUN -- no DB writes.")
        return 0

    # DROP+recreate: migrates any pre-existing PK-less table (an earlier seed
    # created them without the PK that INSERT OR IGNORE needs) and gives a clean
    # full-snapshot reload. The loader always reloads the whole graph, so a
    # single current snapshot is the right model (avoids the MAX(commit-hash)
    # ordering problem of an append model).
    print("DDL: drop + recreate with PRIMARY KEY...")
    execute("DROP TABLE IF EXISTS code_nodes")
    execute("DROP TABLE IF EXISTS code_edges")
    execute(DDL_NODES)
    execute(DDL_EDGES)

    print(f"loading {len(node_rows)} nodes...")
    push("code_nodes", node_rows, args.batch)
    print(f"loading {len(edge_rows)} edges...")
    push("code_edges", edge_rows, args.batch)

    if args.purge_old:
        execute("DELETE FROM code_nodes WHERE built_at_commit <> ?", [commit])
        execute("DELETE FROM code_edges WHERE built_at_commit <> ?", [commit])
        print("purged rows from other commits")

    nc = query("SELECT COUNT(*) AS c FROM code_nodes WHERE built_at_commit = ?", [commit])
    ec = query("SELECT COUNT(*) AS c FROM code_edges WHERE built_at_commit = ?", [commit])
    print(f"verify: code_nodes={nc.get('rows')} code_edges={ec.get('rows')} for {commit}")
    print("Done. Phase 3 (architect graph tools) reads these tables via the bus.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
