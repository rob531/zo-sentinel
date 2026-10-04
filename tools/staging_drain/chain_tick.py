#!/usr/bin/env python3
"""One tick of the staging_drain chain: S1 -> S5(apply) -> S1(affected) -> S3/S6 -> S5(emit) -> S7 -> report.

Deterministic and idempotent: a second tick on an unchanged tree rewrites the same
census, ledger, exclusions and daily line, and emits no new directives (the dedup
set already holds them). S4 (promotion) is deliberately NOT in the tick: it moves
directories and needs the Fly boot test and prod evidence, so it stays a gated
command run by hand or by the tower driver after this tick:

    python tools/promote_staged_to_active.py --enforce --only <name> ... --regenerate

The tower driver (`_chains/staging_drain/`) runs this with `--out-dir
_chains/staging_drain`; in-repo the census lands under artifacts/ (gitignored) and
the ledger/status under chairman/staging_drain/ (committed: the ledger is the one
writer for "why is this still staged").

    python tools/staging_drain/chain_tick.py                  # full tick
    python tools/staging_drain/chain_tick.py --no-apply       # measure + classify + emit only
    python tools/staging_drain/chain_tick.py --no-emit        # no directives written
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
PY = sys.executable


def run(step, cmd, log):
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    entry = {"step": step, "cmd": [os.path.relpath(c, ROOT) if c.startswith(ROOT) else c for c in cmd],
             "rc": proc.returncode, "seconds": round(time.time() - t0, 1),
             "tail": ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-12:]}
    log.append(entry)
    print("[%s] rc=%d %.0fs" % (step, proc.returncode, entry["seconds"]), flush=True)
    for line in entry["tail"]:
        print("    " + line)
    return proc.returncode


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out-dir", default=os.path.join(ROOT, "artifacts", "staging_drain"),
                    help="where census.json and the tick log go")
    ap.add_argument("--ledger", default=os.path.join(ROOT, "chairman", "staging_drain", "ledger.json"))
    ap.add_argument("--directives", default=os.path.join(ROOT, "directives", "pending"))
    ap.add_argument("--no-apply", action="store_true")
    ap.add_argument("--no-emit", action="store_true")
    ap.add_argument("--max-directives", type=int, default=100)
    ap.add_argument("--measure-wall", action="store_true")
    ap.add_argument("--budget-mb", type=int, default=None)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2)))
    args = ap.parse_args(argv)

    os.makedirs(args.out_dir, exist_ok=True)
    census = os.path.join(args.out_dir, "census.json")
    log = []
    sd = lambda n: os.path.join(HERE, n)  # noqa: E731

    rc = run("S1 census", [PY, sd("census.py"), "--out", census, "--workers", str(args.workers), "--quiet"], log)
    if rc:
        return _finish(args, log, rc)
    if not args.no_apply:
        run("S5 mechanical apply", [PY, sd("repair.py"), "--apply", "--census", census, "--ledger", args.ledger], log)
        changed = []
        try:
            with open(os.path.join(args.out_dir, "repair_apply.json"), encoding="utf-8") as fh:
                changed = json.load(fh).get("changed_dirs") or []
        except (OSError, ValueError):
            pass
        if changed:
            run("S1 re-census of repaired", [PY, sd("census.py"), "--out", census, "--merge", census, "--quiet"]
                + [a for n in changed for a in ("--only", n)], log)
    rc = run("S3/S6 classify", [PY, sd("classify.py"), "--census", census, "--ledger", args.ledger, "--quiet"], log)
    if rc:
        return _finish(args, log, rc)
    if not args.no_emit:
        run("S5 emit directives", [PY, sd("repair.py"), "--emit", args.directives, "--census", census,
                                   "--ledger", args.ledger, "--max-directives", str(args.max_directives)], log)
    run("S7 exclusions", [PY, sd("exclusions.py"), "--ledger", args.ledger, "--quiet"], log)
    rep = [PY, sd("report.py"), "--ledger", args.ledger]
    if args.measure_wall:
        rep.append("--measure-wall")
    if args.budget_mb:
        rep += ["--budget-mb", str(args.budget_mb)]
    rc = run("report", rep, log)
    return _finish(args, log, rc)


def _finish(args, log, rc):
    with open(os.path.join(args.out_dir, "tick_log.json"), "w", encoding="utf-8") as fh:
        json.dump({"finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "rc": rc, "steps": log}, fh, indent=1)
    return rc


if __name__ == "__main__":
    sys.exit(main())
