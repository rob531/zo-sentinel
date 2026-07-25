#!/usr/bin/env python3
"""orphanage.py -- give the orphans a home AND a history (chairman ask 2026-07-24).

The reachability ratchet counts orphans (root routers mounted nowhere) and
describes their SHAPE, but never their PROVENANCE: which run/directive/PR emitted
each one, and why it never mounted. Without that history, triage decides from
"that it exists" instead of "why it exists". This tool adds the missing axis.

For every orphan in the census it joins:
  * SHAPE     -- routes / prefix / imports_data_layer / lines / parses (from the ratchet)
  * ORIGIN    -- the commit that first ADDED the file (git log --diff-filter=A --follow):
                 hash, date, author, subject (subject usually carries the PR # / directive)
  * WHY       -- a classification of why it is unmounted, so remit-vs-keep is legible:
                 BROKEN_IMPORT (casing/unknown-symbol -- see model_import_linter),
                 NO_ROUTES, SYNTAX_ERROR, EDIT_CLASS (wire_/integrate_ name),
                 SUPERSEDED (a mounted sibling shares its route), MOUNTABLE (clean,
                 could be promoted), UNKNOWN.

Output: orphanage/manifest.json (full) + a readable ranked summary. This is the
input for the chairman's Fable-5 review: LOAD-BEARING / NECESSARY -> keep + mount;
else -> remit the originating directive. READ-ONLY: mounts nothing, deletes
nothing, changes no orphan.

    python tools/orphanage.py                 # build manifest + print summary
    python tools/orphanage.py --top 25        # longer summary
    python tools/orphanage.py --json          # manifest to stdout
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, "orphanage")
OUT = os.path.join(OUT_DIR, "manifest.json")

sys.path.insert(0, os.path.join(ROOT, "tools"))
import reachability_ratchet as RR  # noqa: E402
try:
    import model_import_linter as LNT  # noqa: E402
except Exception:  # pragma: no cover
    LNT = None

EDIT_CLASS = re.compile(r"^(wire_|integrate_|mount_|patch_)")


def _git(args):
    try:
        return subprocess.run(["git", "-C", ROOT] + args, capture_output=True,
                              text=True, timeout=20).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


_ORIGIN_INDEX = None

def _build_origin_index():
    """ONE git pass: path -> first-add commit. Newest-first log, last-write-wins
    keeps the OLDEST add. Avoids 300+ per-file `git log --follow` calls."""
    idx = {}
    out = _git(["log", "--diff-filter=A", "--name-only",
                "--format=@@@%h\x1f%ci\x1f%an\x1f%s"])
    cur = {"commit": None, "date": None, "author": None, "subject": None}
    for line in out.splitlines():
        if line.startswith("@@@"):
            parts = (line[3:].split("\x1f") + [None] * 4)[:4]
            cur = {"commit": parts[0], "date": parts[1], "author": parts[2], "subject": parts[3]}
        elif line.strip():
            idx[line.strip()] = dict(cur)   # last (oldest) wins
    return idx


def origin_of(module_rel):
    global _ORIGIN_INDEX
    if _ORIGIN_INDEX is None:
        _ORIGIN_INDEX = _build_origin_index()
    return _ORIGIN_INDEX.get(module_rel + ".py",
                             {"commit": None, "date": None, "author": None, "subject": None})


def classify(o, mounted_routes, casing_drift):
    m = o["module"]
    if not o.get("parses", True):
        return "SYNTAX_ERROR"
    if EDIT_CLASS.search(os.path.basename(m)):
        return "EDIT_CLASS"          # wire_/integrate_ -- structurally unbuildable (output_file:null)
    if casing_drift:
        return "BROKEN_IMPORT"       # dead on model-name casing -- model_import_linter --fix revives
    if o.get("route_count", 0) == 0:
        return "NO_ROUTES"
    if any(r in mounted_routes for r in o.get("routes", [])):
        return "SUPERSEDED"          # a mounted sibling already serves its route
    if o.get("imports_data_layer"):
        return "MOUNTABLE"           # clean, wired to real data -- a promotion candidate
    return "UNKNOWN"


def build():
    census = RR.census()
    orphans = census["orphans"]
    # routes served by MOUNTED modules (for SUPERSEDED detection)
    mounted_routes = set()
    norm_map = LNT.build_map(LNT.canonical_models()) if LNT else {}
    for stem in census["mounted"]:
        src = RR._read(os.path.join(ROOT, stem + ".py"))
        for mm in re.finditer(r"@router\.(get|post|put|delete|patch)\(\s*[\"']([^\"']+)", src):
            mounted_routes.add(mm.group(2))

    records = []
    by_why = {}
    for o in orphans:
        src = RR._read(os.path.join(ROOT, o["module"] + ".py"))
        drift = LNT.scan_text(src, norm_map) if norm_map else {}
        why = classify(o, mounted_routes, drift)
        by_why[why] = by_why.get(why, 0) + 1
        records.append({
            "module": o["module"],
            "why_unmounted": why,
            "route_count": o.get("route_count", 0),
            "declared_prefix": o.get("declared_prefix"),
            "imports_data_layer": o.get("imports_data_layer"),
            "lines": o.get("lines"),
            "casing_autofixable": drift or None,
            "origin": origin_of(o["module"]),
        })

    records.sort(key=lambda r: (r["why_unmounted"], -(r["route_count"] or 0)))
    return {
        "orphan_count": len(records),
        "by_why": dict(sorted(by_why.items(), key=lambda kv: -kv[1])),
        "legend": {
            "MOUNTABLE": "clean + data-wired: promotion candidate (keep, mount)",
            "BROKEN_IMPORT": "dead on model-name casing: model_import_linter --fix likely revives",
            "SUPERSEDED": "a mounted sibling already serves its route: remit candidate",
            "NO_ROUTES": "declares no routes: probably not a service: remit candidate",
            "EDIT_CLASS": "wire_/integrate_ name: structurally unbuildable, never a service: remit",
            "SYNTAX_ERROR": "does not parse: broken emission: remit or repair",
            "UNKNOWN": "needs human/Fable-5 judgement",
        },
        "orphans": records,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Orphan provenance manifest (read-only).")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    man = build()
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(man, fh, indent=2)

    if args.json:
        print(json.dumps(man, indent=2)); return 0

    print("=== orphanage: %d orphans, by why-unmounted ===" % man["orphan_count"])
    for why, n in man["by_why"].items():
        print("  %-14s %4d   %s" % (why, n, man["legend"].get(why, "")))
    print("\n  remit candidates (SUPERSEDED + NO_ROUTES + EDIT_CLASS + SYNTAX_ERROR): %d"
          % sum(man["by_why"].get(w, 0) for w in ("SUPERSEDED", "NO_ROUTES", "EDIT_CLASS", "SYNTAX_ERROR")))
    print("  keep/mount candidates (MOUNTABLE + BROKEN_IMPORT[autofix]): %d"
          % sum(man["by_why"].get(w, 0) for w in ("MOUNTABLE", "BROKEN_IMPORT")))
    print("\n  sample (%d), newest-origin first within class:" % args.top)
    for r in sorted(man["orphans"], key=lambda r: (r["why_unmounted"], r["origin"].get("date") or ""))[:args.top]:
        og = r["origin"]
        print("   [%-13s] %-42s %s  (%s)"
              % (r["why_unmounted"], r["module"][:42],
                 (og.get("subject") or "origin?")[:46], (og.get("date") or "")[:10]))
    print("\n  full manifest -> orphanage/manifest.json  (for Fable-5 load-bearing/remit review)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
