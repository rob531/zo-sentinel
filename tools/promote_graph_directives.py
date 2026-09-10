#!/usr/bin/env python3
"""promote_graph_directives.py -- the GATED promote step (the wiring).

Connects the graph-gap adapter into the live build pipeline. The adapter writes
proposal-only directives to graph_directives/; this tool moves HUMAN-APPROVED ones
into directives/proposed/, where proposed_to_pending_promoter picks them up ->
directives/pending/ -> goose_runner.

  graph_directives/  --(this tool, --approve)-->  directives/proposed/
                     --(proposed_to_pending_promoter)-->  pending/  -->  goose

The GATE is here: nothing moves without an explicit --approve/--all. That's the
safety rail -- graph-driven directives are PROPOSAL-ONLY until a human promotes
them; they never auto-build. (Everything downstream of proposed/ is the existing
auto-flow, unchanged.)

  python tools/promote_graph_directives.py                  # list what's pending review
  python tools/promote_graph_directives.py --approve TASK    # promote one (or several)
  python tools/promote_graph_directives.py --all             # promote all pending

On promote: validates the payload, strips needs_review, tags from='graph_gap_approved',
and writes into proposed/ (removing the source). proposed_to_pending_promoter
re-validates before pending/, so a bad payload can't reach goose.
"""
import argparse, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _dirs_root():
    repo = os.path.join(ROOT, "directives")
    return repo if os.path.isdir(repo) else "/home/workspace/zo_sentinel/directives"

ap = argparse.ArgumentParser()
ap.add_argument("tasks", nargs="*", help="task names to promote (with --approve)")
ap.add_argument("--approve", action="store_true", help="promote the named task(s)")
ap.add_argument("--all", action="store_true", help="promote ALL pending review")
ap.add_argument("--graph-dir", default=os.path.join(ROOT, "graph_directives"))
ap.add_argument("--proposed-dir", default=os.path.join(_dirs_root(), "proposed"))
args = ap.parse_args()

# same required fields proposed_to_pending_promoter / directive_validator check
REQUIRED = ("task", "handler", "output_file", "complexity", "description")
VALID_COMPLEXITY = {"high", "medium", "low"}

def validate(d):
    missing = [k for k in REQUIRED if not d.get(k)]
    if missing:
        return False, f"missing {missing}"
    if d.get("complexity") not in VALID_COMPLEXITY:
        return False, f"bad complexity {d.get('complexity')!r}"
    if len(str(d.get("description", ""))) < 50:
        return False, "description too short"
    return True, "ok"

def load_pending():
    out = {}
    if not os.path.isdir(args.graph_dir):
        return out
    for fn in sorted(os.listdir(args.graph_dir)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(args.graph_dir, fn), encoding="utf-8") as f:
                out[fn[:-5]] = json.load(f)
        except Exception as e:
            out[fn[:-5]] = {"_error": str(e)}
    return out

pending = load_pending()

if not args.approve and not args.all:
    # default: list what's awaiting review (the gate is closed)
    if not pending:
        print(f"no graph-gap directives awaiting review in {args.graph_dir}/")
        sys.exit(0)
    print(f"{len(pending)} graph-gap directive(s) awaiting review in {args.graph_dir}/\n")
    for task, d in pending.items():
        ok, msg = validate(d) if "_error" not in d else (False, d["_error"])
        flag = "ok " if ok else "BAD"
        print(f"  [{flag}] [{d.get('gap','?'):12}] {task}")
        print(f"        {str(d.get('description',''))[:110]}")
    print("\npromote with:  python tools/promote_graph_directives.py --approve <task> [<task>...]"
          "\n         or:   python tools/promote_graph_directives.py --all")
    sys.exit(0)

# promote
selected = list(pending) if args.all else args.tasks
if not selected:
    sys.exit("nothing selected -- name task(s) or use --all")
os.makedirs(args.proposed_dir, exist_ok=True)
promoted, rejected = 0, 0
for task in selected:
    d = pending.get(task)
    if d is None:
        print(f"  ?  {task}: not in {args.graph_dir}/"); continue
    ok, msg = validate(d)
    if not ok:
        print(f"  REJECT {task}: {msg}"); rejected += 1; continue
    d.pop("needs_review", None)
    d["from"] = "graph_gap_approved"
    dst = os.path.join(args.proposed_dir, f"{task}.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=2)
    os.remove(os.path.join(args.graph_dir, f"{task}.json"))
    try:
        shown = os.path.relpath(dst, ROOT)
    except ValueError:          # different drive (e.g. test on another mount)
        shown = dst
    print(f"  PROMOTED {task} -> {shown}")
    promoted += 1
print(f"\npromoted {promoted}, rejected {rejected}. "
      f"proposed_to_pending_promoter will move valid ones to pending/ next cycle.")
