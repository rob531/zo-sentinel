#!/usr/bin/env python3
"""pull_check.py -- the "check these pulls" stub.

Run this after `git pull` (or in CI on a PR) to verify the merged state in one
command. It runs the DETERMINISTIC capmap scan and reports the things that
actually matter -- schema drift, orphaned UIs, gap areas -- and EXITS NON-ZERO if
any endpoint touches a table not in schema/app.sql. No duckdb, no network, no LLM:
pure static scan, safe to run anywhere (box, CI, tower).

    python tools/pull_check.py            # human summary, exit 1 if drift
    python tools/pull_check.py --quiet    # just the verdict line + exit code

Drift here = the class of bug the knowledge layer keeps catching (an endpoint
reading/writing the wrong table, e.g. mesh_events instead of mcp_submissions).
"""
import json, os, sys, tempfile, importlib.util

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUIET = "--quiet" in sys.argv


def _run_scan(out_path):
    spec = importlib.util.spec_from_file_location("scan_capmap",
                                                  os.path.join(ROOT, "tools", "scan_capmap.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.argv = ["scan_capmap", out_path]          # scan_capmap reads argv[1] as out
    spec.loader.exec_module(mod)                   # defines OUT from argv[1]
    mod.main()                                     # run it (its __name__ guard won't fire on import)


def main():
    tmp = os.path.join(tempfile.gettempdir(), "pull_check_capmap.json")
    _run_scan(tmp)
    cap = json.load(open(tmp, encoding="utf-8"))

    drift, orphans, gap_areas = [], [], []
    for a in cap["areas"]:
        for e in a["endpoints"]:
            if "DRIFT" in e.get("io", ""):
                drift.append((a["name"], f"{e['method']} {e['path']}", e["io"].split("DRIFT:")[1].rstrip("] ")))
        for u in a["ui"]:
            if not u["served"]:
                orphans.append((a["name"], u["file"]))
        if a["status"] == "gap":
            gap_areas.append(a["name"])

    s = cap["summary"]
    if not QUIET:
        print(f"\n=== pull check @ {ROOT} ===")
        print(f"  endpoints: {s['endpoints_total']}  |  UIs orphaned: {len(orphans)}  |  "
              f"areas {s['areas_complete']}c/{s['areas_partial']}p/{s['areas_gap']}g")
        if drift:
            print(f"\n  SCHEMA DRIFT ({len(drift)}) -- endpoint touches a table not in app.sql:")
            for area, ep, tbl in drift:
                print(f"    [{area}] {ep} ->{tbl}")
        if orphans:
            print(f"\n  ORPHANED UIs ({len(orphans)}) -- no serving route / not in shell UI_FILES:")
            for area, f in orphans:
                print(f"    [{area}] {f}")
        if gap_areas:
            print(f"\n  GAP areas (no wired endpoint): {', '.join(gap_areas)}")

    verdict = "DRIFT" if drift else ("OK-with-orphans" if orphans else "CLEAN")
    print(f"\nverdict: {verdict}  (drift={len(drift)} orphaned_ui={len(orphans)} gap_areas={len(gap_areas)})")
    return 1 if drift else 0


if __name__ == "__main__":
    sys.exit(main())
