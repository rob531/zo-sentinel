#!/usr/bin/env python3
"""graph_gap_directives.py -- (b) the DIRECTIVE-SOURCE ADAPTER.

Turns VERIFIED gaps in the app-model graph into builder directives, so the
directive generator stops re-deriving from a static spec and starts converging
on real, observed gaps. Three guards make this safe + non-churny:

  1. GROUNDED  -- only emits for gaps the graph actually shows (orphaned UIs that
                  are NOT observed-active; endpoints touching tables not in app.sql).
  2. DEDUPED   -- excludes UIs the same-origin shell ALREADY serves
                  (app_routes.UI_FILES) and skips tasks already emitted. This is the
                  convergence: it will NOT re-propose what piece A already fixed.
  3. PROPOSAL-ONLY -- writes to a review dir with from='graph_gap',
                  needs_review=true. It never auto-feeds pending/ -- a human (or a
                  promote step) moves approved ones in. Graph-driven != auto-build.

    python tools/graph_gap_directives.py [--graph schema/app_graph.sql]
        [--observed app_observed_edges.ndjson] [--out graph_directives]

Matches gen_directives.py's payload schema so the existing pipeline consumes them.
"""
import json, os, re, sys, argparse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
import app_routes  # for UI_FILES (what the shell already serves) -- the dedup source

ap = argparse.ArgumentParser()
ap.add_argument("--graph", default=os.path.join(ROOT, "schema", "app_graph.sql"))
ap.add_argument("--observed", default=os.path.join(ROOT, "app_observed_edges.ndjson"))
ap.add_argument("--out", default=os.path.join(ROOT, "graph_directives"))
args = ap.parse_args()

try:
    import duckdb
except ImportError:
    sys.exit("needs the duckdb module (pip install duckdb) OR load app_node/app_edge "
             "into write_service and query via the bus")

con = duckdb.connect()
con.execute(open(args.graph, encoding="utf-8").read())
# fold in observed edges if the loader has produced them (flips false-positives out)
obs_sql = os.path.join(ROOT, "schema", "app_graph_observed.sql")
if os.path.isfile(args.observed) and os.path.isfile(obs_sql):
    con.execute(open(obs_sql, encoding="utf-8").read())
    orphan_view = "gap_orphaned_ui_true"     # observed-confirmed real orphans
else:
    orphan_view = "gap_orphaned_ui"          # static only (no observed data yet)

SHELL_SERVES = {fn for fn, _ in app_routes.UI_FILES.values()}

def slug(s):
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")

directives = []

# --- orphaned UIs (minus what the shell already serves) ----------------------
for area, ui in con.execute(f"SELECT area, ui_page FROM {orphan_view} ORDER BY area").fetchall():
    if ui in SHELL_SERVES:
        continue  # DEDUP: piece A already serves it -- not a gap
    directives.append({
        "task": f"serve_ui_{slug(ui)}",
        "handler": "generate_file", "output_file": "app_routes.py",
        "complexity": "low", "phase": "ui", "priority": 0.55,
        "reads": ["app_routes.py", "app_shell.py", ui],
        "description": (
            f"The UI page '{ui}' (area: {area}) is orphaned -- no same-origin route "
            f"serves it. Add it to app_routes.UI_FILES mapping a clean URL to '{ui}', "
            f"matching how the shell already serves the other pages. IF '{ui}' is a "
            f"superseded duplicate (e.g. an older mcp_detail_view variant), do NOT wire "
            f"it -- mark it for archive/ instead and say so. Do not add a localhost URL."),
        "from": "graph_gap", "needs_review": True, "gap": "orphaned_ui",
    })

# --- schema drift: endpoint touches a table not in app.sql --------------------
drift = con.execute("""
    SELECT DISTINCT ep.area, ep.name, ep.file, t.name AS bad_table
    FROM app_node ep
    JOIN app_edge e ON e.src = ep.id AND e.rel IN ('reads','writes')
    JOIN app_node t ON t.id = e.dst AND t.detail LIKE '%drift%'
    WHERE ep.kind = 'endpoint' ORDER BY ep.area
""").fetchall()
HINT = {"mesh_events": "mcp_submissions", "policy_rules": "mcp_policy_rules"}
for area, ep, file, bad in drift:
    fix = HINT.get(bad)
    directives.append({
        "task": f"fix_table_{slug(ep)}_{slug(bad)}",
        "handler": "generate_file", "output_file": file or "ui_server.py",
        "complexity": "low", "phase": "fix", "priority": 0.62,
        "reads": [file or "ui_server.py", "schema/app.sql"],
        "description": (
            f"Endpoint '{ep}' (area: {area}, file: {file}) reads/writes the table "
            f"'{bad}', which is NOT in schema/app.sql. "
            + (f"Repoint it to the canonical table '{fix}'. " if fix else
               f"Either repoint it to the correct canonical table, OR -- if '{bad}' is a "
               f"real intended table -- add it to schema/app.sql with a PK. ")
            + "Verify column names against schema/app.sql too."),
        "from": "graph_gap", "needs_review": True, "gap": "schema_drift",
    })

# --- write proposal-only (skip if already in flight ANYWHERE) ----------------
# Dedup across the whole pipeline so a promoted/in-flight gap is never re-emitted:
# graph_directives/ (awaiting review) + directives/{proposed,pending}/ (promoted).
# Once the fix lands, capmap regenerates and the gap disappears on its own.
def _dirs_root():
    repo = os.path.join(ROOT, "directives")
    return repo if os.path.isdir(repo) else "/home/workspace/zo_sentinel/directives"

_INFLIGHT = [args.out,
             os.path.join(_dirs_root(), "proposed"),
             os.path.join(_dirs_root(), "pending")]

def _in_flight(task):
    return any(os.path.exists(os.path.join(d, f"{task}.json")) for d in _INFLIGHT)

os.makedirs(args.out, exist_ok=True)
written, skipped = 0, 0
for d in directives:
    fp = os.path.join(args.out, f"{d['task']}.json")
    if _in_flight(d["task"]):
        skipped += 1
        continue
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    written += 1

print(f"app-model graph -> {len(directives)} grounded gaps "
      f"({sum(1 for d in directives if d['gap']=='orphaned_ui')} orphaned-UI, "
      f"{sum(1 for d in directives if d['gap']=='schema_drift')} schema-drift)")
print(f"  shell already serves {len(SHELL_SERVES)} UIs -> excluded from gaps (dedup vs piece A)")
print(f"  wrote {written} proposal-only directives to {args.out}/ (skipped {skipped} existing)")
print(f"  using orphan source: {orphan_view}")
for d in directives:
    print(f"    - [{d['gap']:12}] {d['task']}")
