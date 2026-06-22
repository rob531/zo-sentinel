#!/usr/bin/env python3
"""graph_refresh.py -- self-healing graph indexer (build A: "graph auto-refresh on merge").

Keeps DuckDB code_nodes in sync with the DEPLOYED repo HEAD. When the git HEAD differs
from the graph's built_at_commit, it rebuilds graphify-out/graph.json (index_graph.py)
and loads it to the :8772 bus (load_graph_to_bus.py --purge-old). GitHub runners can't
reach the local bus, so this runs ZoCompute-side: refresh_code deploys new code, the next
graph_refresh cycle re-indexes -> the architect's list_domains/graph_neighbors (#390) and
loop_watch stop reading a stale graph. Pairs with loop_watch (which ALERTS if the graph
stays stale because this failed).

    python3 tools/graph_refresh.py               # one-shot: reindex iff stale
    python3 tools/graph_refresh.py --force       # reindex regardless
    python3 tools/graph_refresh.py --interval 900  # daemon: poll every 15 min
"""
import argparse, json, os, subprocess, sys, time, urllib.request

ROOT = os.environ.get("ZO_SENTINEL_DIR", "/home/workspace/zo_sentinel")
BUS  = os.environ.get("ZO_WRITE_SERVICE", "http://127.0.0.1:8772") + "/query"
PY   = sys.executable or "python3"
INDEX_TIMEOUT = int(os.environ.get("GR_INDEX_TIMEOUT", 2000))
LOAD_TIMEOUT  = int(os.environ.get("GR_LOAD_TIMEOUT", 600))
IDLE_MIN      = int(os.environ.get("GR_IDLE_MIN", 8))   # defer reindex while a build is active


def needs_refresh(head: str, graph_commit: str, force: bool = False) -> bool:
    """PURE: reindex if forced, or if we know HEAD and it differs from the graph's commit.
    If HEAD is unknown (no git) we do NOT churn. A missing graph (no commit) = refresh."""
    if force:
        return True
    if not head:
        return False
    if not graph_commit:
        return True
    return head[:12] != graph_commit[:12]


def _git_head():
    try:
        return subprocess.run(["git", "-C", ROOT, "rev-parse", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return ""

def _graph_commit():
    try:
        req = urllib.request.Request(BUS, data=json.dumps(
            {"sql": "SELECT built_at_commit AS c, COUNT(*) AS n FROM code_nodes "
                    "GROUP BY built_at_commit ORDER BY n DESC LIMIT 1"}).encode(),
            headers={"content-type": "application/json"}, method="POST")
        d = json.loads(urllib.request.urlopen(req, timeout=8).read().decode("utf-8", "replace"))
        rows = d.get("rows", []) if isinstance(d, dict) else (d or [])
        return (rows[0]["c"] if rows else "") or ""
    except Exception:
        return ""


def _builder_active():
    """True if a build_artifact landed within IDLE_MIN minutes. We DEFER the heavy
    DROP+recreate+bulk-load while a build is active so it never contends with the build's
    write burst on the single writer (write_service). Mirrors the architect's idle gate.
    Best-effort -> False (don't block on a read failure)."""
    try:
        from datetime import datetime, timezone
        req = urllib.request.Request(BUS, data=json.dumps(
            {"sql": "SELECT MAX(created_at) AS ts FROM mesh_memory WHERE memory_type='build_artifact'"}).encode(),
            headers={"content-type": "application/json"}, method="POST")
        rows = json.loads(urllib.request.urlopen(req, timeout=6).read().decode("utf-8","replace")).get("rows", [])
        ts = rows[0]["ts"] if rows else None
        if not ts:
            return False
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(str(ts).replace("Z","+00:00"))).total_seconds()/60.0
        return age < IDLE_MIN
    except Exception:
        return False


def refresh(force=False):
    head, gc = _git_head(), _graph_commit()
    if not needs_refresh(head, gc, force):
        print(f"[graph_refresh] up to date (graph at {head[:8] or '?'})")
        return 0
    if not force and _builder_active():
        print("[graph_refresh] STALE but a build is active -- deferring reindex (protect write_service)")
        return 0
    print(f"[graph_refresh] STALE repo={head[:8] or '?'} graph={gc[:8] or 'none'} -> reindexing")
    try:
        r1 = subprocess.run([PY, os.path.join(ROOT, "tools", "index_graph.py"), "--root", ROOT],
                            cwd=ROOT, timeout=INDEX_TIMEOUT)
    except subprocess.TimeoutExpired:
        print("[graph_refresh] index_graph TIMEOUT", file=sys.stderr); return 1
    if r1.returncode != 0:
        print(f"[graph_refresh] index_graph rc={r1.returncode}", file=sys.stderr); return 1
    try:
        r2 = subprocess.run([PY, os.path.join(ROOT, "tools", "load_graph_to_bus.py"), "--purge-old"],
                            cwd=ROOT, timeout=LOAD_TIMEOUT)
    except subprocess.TimeoutExpired:
        print("[graph_refresh] load_graph_to_bus TIMEOUT", file=sys.stderr); return 1
    if r2.returncode != 0:
        print(f"[graph_refresh] load_graph_to_bus rc={r2.returncode}", file=sys.stderr); return 1
    print(f"[graph_refresh] OK -- graph now at {head[:8]}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="reindex regardless of staleness")
    ap.add_argument("--interval", type=int, default=0, help="daemon loop seconds (0 = one-shot)")
    a = ap.parse_args()
    if a.interval > 0:
        while True:
            try:
                refresh(a.force)
            except Exception as e:
                print("[graph_refresh] cycle error:", e, file=sys.stderr)
            time.sleep(a.interval)
    else:
        sys.exit(refresh(a.force))
