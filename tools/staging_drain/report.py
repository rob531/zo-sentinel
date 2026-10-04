#!/usr/bin/env python3
"""The daily line for the chairman one-pager, derived from the ledger. Nothing else.

    staging_drain: promoted N · superseded N · repairing N · retired N · remaining N · wall: <none|image size ...>

  repairing  = repair rows with an emitted directive (an OPEN builder directive)
  remaining  = promotable (gate green, no prod evidence yet) + repair rows with no
               directive yet + census errors. A promotable row is NOT promoted.
  wall       = the image-size wall, measured as the cumulative RSS of importing every
               promotable api service into ONE interpreter (what the Fly image pays),
               when `--measure-wall` is passed; otherwise reported UNKNOWN. Never 'none'
               unless it was measured and fits the declared budget.

Also writes chairman/staging_drain/STATUS.md (the section chairman_daily_brief.py reads)
and chairman/staging_drain/daily_line.txt.

    python tools/staging_drain/report.py [--measure-wall] [--budget-mb 512]
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DEFAULT_LEDGER = os.path.join(ROOT, "chairman", "staging_drain", "ledger.json")
DEFAULT_STATUS = os.path.join(ROOT, "chairman", "staging_drain", "STATUS.md")
DEFAULT_LINE = os.path.join(ROOT, "chairman", "staging_drain", "daily_line.txt")

_WALL_CODE = r"""
import importlib, json, resource, sys
out = []
for mod in sys.argv[1:]:
    try:
        importlib.import_module(mod)
        ok = True
    except Exception as exc:
        ok = repr(exc)[:200]
    out.append([mod, resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, ok])
print(json.dumps(out))
"""


def measure_wall(names, timeout=600):
    """Cumulative max RSS (KiB) importing every promotable api router in one process."""
    mods = ["services.staged.%s.router" % n for n in names]
    try:
        proc = subprocess.run([sys.executable, "-c", "import fastapi\n" + _WALL_CODE] + mods, cwd=ROOT,
                              capture_output=True, text=True, timeout=timeout,
                              env={**os.environ, "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")})
        rows = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:  # noqa: BLE001 -- reported, never zero-filled
        return {"status": "unmeasured", "detail": repr(exc)}
    if sys.platform == "darwin":
        rows = [[m, kb // 1024, ok] for m, kb, ok in rows]
    return {"status": "measured", "services": len(rows), "final_rss_kb": rows[-1][1] if rows else None,
            "per_service": [{"module": m, "rss_kb_after": kb, "import_ok": ok} for m, kb, ok in rows]}


def counts(ledger):
    c = {"promoted": 0, "superseded": 0, "repairing": 0, "retired": 0, "remaining": 0,
         "promotable_api": 0, "promotable_worker": 0, "repair_no_directive": 0, "repair_mechanical": 0}
    for row in ledger["services"].values():
        o = row["outcome"]
        if o in ("promoted", "superseded", "retired"):
            c[o] += 1
        elif o == "repair":
            if row.get("directive"):
                c["repairing"] += 1
            else:
                c["remaining"] += 1
                c["repair_no_directive"] += 1
                if str(row.get("repair_path", "")).startswith("mechanical"):
                    c["repair_mechanical"] += 1
        else:
            c["remaining"] += 1
            if o == "promotable":
                c["promotable_api" if row.get("runtime_class") == "api" else "promotable_worker"] += 1
    c["total"] = len(ledger["services"])
    return c


def daily_line(c, wall):
    if wall.get("status") == "measured" and wall.get("final_rss_kb") is not None:
        mb = wall["final_rss_kb"] // 1024
        budget = wall.get("budget_mb")
        meas = "%d promotable api services import to %d MiB in one process" % (wall["services"], mb)
        if budget and mb <= budget:
            w = "none (%s, budget %d MiB)" % (meas, budget)
        elif budget:
            w = "image size (%s, budget %d MiB)" % (meas, budget)
        else:
            w = "image size UNKNOWN budget (%s; pass --budget-mb)" % meas
    else:
        w = "image size UNKNOWN (not measured in this tree: %s)" % wall.get("detail", "pass --measure-wall")
    return ("staging_drain: promoted %d · superseded %d · repairing %d · retired %d · remaining %d · wall: %s"
            % (c["promoted"], c["superseded"], c["repairing"], c["retired"], c["remaining"], w))


def status_md(ledger, c, wall, line):
    fc = {}
    for row in ledger["services"].values():
        if row["outcome"] == "repair":
            fc[row.get("failure_class")] = fc.get(row.get("failure_class"), 0) + 1
    prom = sorted(n for n, r in ledger["services"].items() if r["outcome"] == "promotable")
    vuln_prom = [n for n in prom if ledger["services"][n].get("vulnerability_family")]
    lines = [
        "# staging_drain — status (derived; writer: tools/staging_drain/report.py)",
        "",
        "Generated %s from ledger %s (census head `%s`)." % (
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), ledger.get("generated_at"),
            (ledger.get("basis") or {}).get("census_git_head")),
        "",
        "```", line, "```",
        "",
        "| outcome | n |", "|---|---|",
        "| promoted (live in prod, evidence recorded) | %d |" % c["promoted"],
        "| superseded (newer version or an active service owns it) | %d |" % c["superseded"],
        "| repairing (open builder directive) | %d |" % c["repairing"],
        "| retired (no source; reason recorded; nothing deleted) | %d |" % c["retired"],
        "| remaining | %d |" % c["remaining"],
        "| ↳ promotable api, awaiting S4 (Fly image + boot test + prod drift) | %d |" % c["promotable_api"],
        "| ↳ promotable worker/lib, awaiting S4 (zo scheduled job; zo→prod DB reach unproven) | %d |" % c["promotable_worker"],
        "| ↳ repair rows without a directive yet (cap or mechanical) | %d |" % c["repair_no_directive"],
        "| total staged directories | %d |" % c["total"],
        "",
        "Repair rows by gate failure class: " + ", ".join("%s %d" % kv for kv in sorted(fc.items(), key=lambda kv: -kv[1])),
        "",
        "Promotable now (%d; vulnerability families first: %s): %s" % (
            len(prom), ", ".join(vuln_prom) or "none", ", ".join(prom) or "none"),
        "",
        "Wall: %s" % json.dumps({k: v for k, v in wall.items() if k != "per_service"}),
    ]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--status", default=DEFAULT_STATUS)
    ap.add_argument("--line", default=DEFAULT_LINE)
    ap.add_argument("--measure-wall", action="store_true")
    ap.add_argument("--budget-mb", type=int, default=None, help="declared image memory budget, MiB")
    args = ap.parse_args(argv)
    if not os.path.isfile(args.ledger):
        print("staging_drain: UNKNOWN (no ledger at %s)" % args.ledger)
        return 2
    with open(args.ledger, encoding="utf-8") as fh:
        ledger = json.load(fh)
    c = counts(ledger)
    wall = {"status": "unmeasured", "detail": "pass --measure-wall"}
    if args.measure_wall:
        prom = sorted(n for n, r in ledger["services"].items() if r["outcome"] == "promotable" and r.get("runtime_class") == "api")
        wall = measure_wall(prom)
        wall["budget_mb"] = args.budget_mb
    line = daily_line(c, wall)
    os.makedirs(os.path.dirname(os.path.abspath(args.status)), exist_ok=True)
    with open(args.status, "w", encoding="utf-8") as fh:
        fh.write(status_md(ledger, c, wall, line))
    with open(args.line, "w", encoding="utf-8") as fh:
        fh.write(line + "\n")
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
