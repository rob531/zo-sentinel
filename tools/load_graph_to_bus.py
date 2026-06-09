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
import re
import subprocess
import sys
import time
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
    "repo VARCHAR, id VARCHAR, label VARCHAR, norm_label VARCHAR, file_type VARCHAR, "
    "source_file VARCHAR, source_location VARCHAR, community INTEGER, "
    "built_at_commit VARCHAR, PRIMARY KEY (repo, id, built_at_commit))"
)
DDL_EDGES = (
    "CREATE TABLE IF NOT EXISTS code_edges ("
    "repo VARCHAR, src VARCHAR, dst VARCHAR, relation VARCHAR, weight DOUBLE, "
    "confidence VARCHAR, confidence_score DOUBLE, source_file VARCHAR, "
    "source_location VARCHAR, built_at_commit VARCHAR, "
    "PRIMARY KEY (repo, src, dst, relation, built_at_commit))"
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


def load_graph(graph_path):
    data = json.loads(pathlib.Path(graph_path).read_text(encoding="utf-8"))
    nodes = data.get("nodes", [])
    links = data.get("links", data.get("edges", []))
    # Prefer the commit the graph was built at; fall back to current HEAD.
    commit = data.get("built_at_commit") or git_head()
    return nodes, links, commit


def node_row(n, commit, repo):
    return {
        "repo": repo,
        "id": _s(n.get("id")),
        "label": _s(n.get("label")),
        "norm_label": _s(n.get("norm_label") or (n.get("label") or "").lower()),
        "file_type": _s(n.get("file_type")),
        "source_file": _s(n.get("source_file")),
        "source_location": _s(n.get("source_location")),
        "community": _i(n.get("community")),
        "built_at_commit": commit,
    }


def edge_row(e, commit, repo):
    return {
        "repo": repo,
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


def _is_archived(source_file) -> bool:
    """A node under an archive/ directory -- retired code we deliberately keep on
    disk for reversibility but do NOT want memorialized in the knowledge layer.
    graphify itself honors .graphifyignore (archive/), but this is the belt-and-
    suspenders at the layer the agent tools actually read (DuckDB): a graph built
    without the ignore file still can't leak archived code into code_nodes."""
    if not source_file:
        return False
    sf = str(source_file).replace("\\", "/")
    return sf.startswith("archive/") or "/archive/" in sf


def _drop_archived(node_rows, edge_rows):
    """Remove archive/ nodes and any edge incident to one. Returns
    (nodes, edges, dropped_nodes, dropped_edges)."""
    kept_nodes = [n for n in node_rows if not _is_archived(n.get("source_file"))]
    kept_ids = {n["id"] for n in kept_nodes}
    kept_edges = [e for e in edge_rows
                  if e.get("src") in kept_ids and e.get("dst") in kept_ids]
    return (kept_nodes, kept_edges,
            len(node_rows) - len(kept_nodes), len(edge_rows) - len(kept_edges))


def _clean(rows, required, pk):
    """Drop rows with a NULL/empty value in any PK column, then dedupe on the PK
    tuple. This keeps the data PK-clean so write_service's INSERT OR IGNORE never
    exercises its NULL/conflict path -- which, on the box's larger graph, hit a
    DuckDB internal assertion. Returns (clean_rows, dropped_null, dropped_dup)."""
    seen, out, dn, dd = set(), [], 0, 0
    for r in rows:
        if any(r.get(c) in (None, "") for c in required):
            dn += 1
            continue
        k = tuple(r.get(c) for c in pk)
        if k in seen:
            dd += 1
            continue
        seen.add(k)
        out.append(r)
    return out, dn, dd


# --- bus helpers (lazy requests import so --dry-run needs no deps) ------------
def _post(path, payload, timeout):
    import requests
    r = requests.post(f"{WS}{path}", json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()


def execute(sql, params=None):
    return _post("/execute", {"sql": sql, "params": params or [],
                              "agent_id": "graph_loader", "wait": True}, 60)


def write_batch(table, rows, attempts=5):
    """POST a batch with retry+backoff. The single writer also serves the live
    trust pipeline + periodic CHECKPOINTs, so a batch can occasionally exceed
    write_service's 10s wait -> 502. Retrying is SAFE here: INSERT OR IGNORE +
    the PK make a re-send idempotent (any rows that did land are skipped)."""
    last = None
    for k in range(attempts):
        try:
            return _post("/write", {"table": table, "rows": rows,
                                    "agent_id": "graph_loader", "wait": True}, 60)
        except Exception as e:
            last = e
            time.sleep(min(0.5 * (2 ** k), 8.0))   # 0.5,1,2,4,8s -- let the writer drain
    raise last


def query(sql, params=None):
    return _post("/query", {"sql": sql, "params": params or []}, 30)


def push(table, rows, batch):
    for i in range(0, len(rows), batch):
        write_batch(table, rows[i:i + batch])
        print(f"  {table}: {min(i + batch, len(rows))}/{len(rows)}", flush=True)
        time.sleep(0.15)   # don't monopolise the single writer; let others interleave


def _write_ndjson(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r))
            f.write("\n")


def _bulk_insert(table, ndjson_path, select_cols, expected, count_where=""):
    """ONE `INSERT ... SELECT FROM read_json_auto(...)` over the bus instead of
    ~150 batched POSTs. ~100x faster and far gentler on the single writer (one
    operation, not a batch storm that contends with the live trust pipeline and
    triggered timeouts/502s/assertions). Plain INSERT (data is _clean'd) avoids
    the INSERT OR IGNORE conflict path entirely. Verifies by COUNT (scoped to
    count_where so a per-repo append can't false-pass on another repo's rows)."""
    p = str(ndjson_path).replace("\\", "/")
    try:
        execute(f"INSERT INTO {table} SELECT {select_cols} FROM read_json_auto('{p}')")
    except Exception as e:
        print(f"  ({table}: execute returned {type(e).__name__}; verifying by count)")
    n = 0
    for _ in range(20):
        rows = query(f"SELECT COUNT(*) AS c FROM {table} {count_where}").get("rows", [])
        n = rows[0].get("c", 0) if rows else 0
        if n >= expected:
            print(f"  {table}: {n} rows loaded")
            return True
        time.sleep(3)
    print(f"  WARN {table}: {n}/{expected} rows after wait")
    return False


def main(argv=None):
    ap = argparse.ArgumentParser(description="Load graphify graph.json into DuckDB via :8772")
    ap.add_argument("--dry-run", action="store_true", help="parse + count only; no writes")
    ap.add_argument("--repo", default="zo_sentinel", help="repo tag for this graph (default zo_sentinel)")
    ap.add_argument("--graph", default=str(GRAPH), help="path to graph.json (default this repo's)")
    ap.add_argument("--keep", action="store_true",
                    help="append this --repo without dropping the tables (re-loads only this repo)")
    ap.add_argument("--batch", type=int, default=500, help="rows per /write POST (default 500)")
    ap.add_argument("--purge-old", action="store_true",
                    help="after load, delete this repo's rows from other commits")
    args = ap.parse_args(argv)

    if not re.match(r"^[A-Za-z0-9_]+$", args.repo):
        print(f"ERROR: --repo must be alphanumeric/underscore, got {args.repo!r}", file=sys.stderr)
        return 2
    graph_path = pathlib.Path(args.graph)
    if not graph_path.is_file():
        print(f"ERROR: {graph_path} not found -- build it first (graphify update <root>)",
              file=sys.stderr)
        return 2

    nodes, links, commit = load_graph(graph_path)
    node_rows = [node_row(n, commit, args.repo) for n in nodes]
    edge_rows = [edge_row(e, commit, args.repo) for e in links]
    print(f"{graph_path.name} [repo={args.repo}]: {len(node_rows)} nodes, "
          f"{len(edge_rows)} edges; built_at_commit={commit}")

    # Never memorialize archived code: drop archive/ nodes + incident edges. The
    # knowledge layer should mirror LIVE code only (graphify's .graphifyignore is
    # the first gate; this guards a graph built without it).
    node_rows, edge_rows, an, ae = _drop_archived(node_rows, edge_rows)
    if an or ae:
        print(f"excluded archive/: -{an} nodes, -{ae} incident edges")

    # PK-clean the data (drop NULL-PK rows + dedupe) so INSERT OR IGNORE never
    # hits the NULL/conflict path that crashed on the box's edge set.
    node_rows, nn, nd = _clean(node_rows, ("id",), ("repo", "id", "built_at_commit"))
    edge_rows, en, ed = _clean(edge_rows, ("src", "dst", "relation"),
                               ("repo", "src", "dst", "relation", "built_at_commit"))
    if nn or nd or en or ed:
        print(f"cleaned: nodes -{nn} null/-{nd} dup -> {len(node_rows)}; "
              f"edges -{en} null/-{ed} dup -> {len(edge_rows)}")

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
    if args.keep:
        # Append this repo: keep the tables; clear only THIS repo's rows so the
        # other repo's graph survives. Idempotent re-load of one --repo.
        print(f"DDL: ensure tables; clearing prior rows for repo={args.repo}...")
        execute(DDL_NODES)
        execute(DDL_EDGES)
        execute("DELETE FROM code_nodes WHERE repo = ?", [args.repo])
        execute("DELETE FROM code_edges WHERE repo = ?", [args.repo])
    else:
        # Fresh load: drop + recreate (also migrates an older repo-less/PK-less
        # schema). Use this for the FIRST repo; add others with --keep.
        print("DDL: drop + recreate with PRIMARY KEY...")
        execute("DROP TABLE IF EXISTS code_nodes")
        execute("DROP TABLE IF EXISTS code_edges")
        execute(DDL_NODES)
        execute(DDL_EDGES)

    # Bulk load: write repo-scoped NDJSON, then ONE read_json INSERT per table
    # (gentle on the single writer; no batch storm). Files live next to the
    # graph so write_service (same box, /home/workspace) can read them.
    cw = f"WHERE repo='{args.repo}'"
    nf = graph_path.parent / f"_{args.repo}_nodes.ndjson"
    ef = graph_path.parent / f"_{args.repo}_edges.ndjson"
    _write_ndjson(nf, node_rows)
    _write_ndjson(ef, edge_rows)
    print(f"bulk-loading {len(node_rows)} nodes + {len(edge_rows)} edges via read_json...")
    _bulk_insert("code_nodes", nf,
                 "repo,id,label,norm_label,file_type,source_file,source_location,"
                 "CAST(community AS INTEGER),built_at_commit", len(node_rows), cw)
    _bulk_insert("code_edges", ef,
                 "repo,src,dst,relation,CAST(weight AS DOUBLE),confidence,"
                 "CAST(confidence_score AS DOUBLE),source_file,source_location,built_at_commit",
                 len(edge_rows), cw)

    if args.purge_old:
        execute("DELETE FROM code_nodes WHERE repo = ? AND built_at_commit <> ?", [args.repo, commit])
        execute("DELETE FROM code_edges WHERE repo = ? AND built_at_commit <> ?", [args.repo, commit])
        print("purged rows from other commits of this repo")

    nc = query("SELECT COUNT(*) AS c FROM code_nodes WHERE repo = ?", [args.repo])
    ec = query("SELECT COUNT(*) AS c FROM code_edges WHERE repo = ?", [args.repo])
    tot = query("SELECT COUNT(DISTINCT repo) AS r, COUNT(*) AS n FROM code_nodes")
    print(f"verify: repo={args.repo} code_nodes={nc.get('rows')} code_edges={ec.get('rows')}; "
          f"all repos: {tot.get('rows')}")
    print("Done. Phase 3 (architect graph tools) reads these tables via the bus.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
