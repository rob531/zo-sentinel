#!/usr/bin/env python3
"""FU-568 evidence: the negative controls and the blast radius, re-runnable.

Two questions a reviewer of the emission-time column gate must be able to ask
again later, answered by running this rather than by reading a paragraph:

  --controls       Do the FU-568 assertions actually go RED? Six defective
                   builds of the SHIPPED helper, each aimed at one control.
                   An assertion never observed red is UNPROVEN, not passing
                   (HARNESS_DOCTRINE R4).

  --blast-radius   How many builds would this gate have refused? A gate that
                   stalls the daily ladder gets switched off, and switched-off
                   gates are what the 2026-07-28 ruling was about. Also checks
                   every refusal against `referent_verify` -- the single judge
                   -- so a disagreement shows up as a false positive rather
                   than as a blocked build nobody can explain.

Not wired into any workflow. `referent_verify.py` remains the single judge and
`register_build` remains the single emission point; this only measures them.
Exit 0 all good, 1 a control was vacuous or a false positive appeared, 2 the
check could not be run at all (never a pass).
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "mcp_servers" / "builder_mcp.py"
RV = ROOT / "tools" / "referent_verify.py"
TEST = "tests/test_fu568_register_build_phantom_columns.py"


def _load_rv():
    if not RV.is_file():
        return None
    spec = importlib.util.spec_from_file_location("_rv_fu568_evidence", RV)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_rv_fu568_evidence"] = mod
    spec.loader.exec_module(mod)
    return mod


def _helpers(src: str):
    """Exec the shipped pure helpers in a stdlib-only namespace."""
    ns: dict = {"ast": ast}
    tree = ast.parse(src)
    lines = src.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in (
                "_phantom_column_refs", "_reject_phantom_columns"):
            exec(compile("\n".join(lines[node.lineno - 1: node.end_lineno]),
                         f"<{node.name}>", "exec"), ns)      # noqa: S102
    return ns


# --------------------------------------------------------------------------
# --controls
# --------------------------------------------------------------------------

MUTATIONS = [
    ("no call site -- present and never consulted (the lane_halt shape)",
     "    _bad_cols = _reject_phantom_columns(target_file, content, _catalog,\n"
     "                                        _iter_sql, _extract)\n"
     "    if _bad_cols:\n        return _bad_cols\n",
     "    _bad_cols = \"\"\n",
     "test_register_build_calls_the_guard"),

    ("constant verdict -- always allows",
     "    bad = _phantom_column_refs(content, catalog, iter_sql, extract_refs)\n",
     "    bad = []\n",
     "test_refuses_the_real_20260918_payload"),

    ("double-judges a table on NO plane (the armed TABLES check owns it)",
     "            if real is None or col in real:\n                continue\n",
     "            if real is not None and col in real:\n                continue\n"
     "            real = real or []\n",
     "test_does_not_double_judge_an_unknown_table"),

    ("FAIL-CLOSED on an unresolvable catalog -- stops every build on the host",
     "    if not catalog or iter_sql is None or extract_refs is None:\n        return []\n",
     "    if not catalog or iter_sql is None or extract_refs is None:\n"
     "        return [('unknown', 'unknown', [])]\n",
     "test_empty_catalog_allows_and_never_refuses"),

    ("refusal carries no correction -- a wall, not a cure (R7)",
     'lines.append(f"  {table}.{col} -- {table} has no such column. "\n'
     '                     f"Real columns: {shown}")',
     'lines.append(f"  {table}.{col} is wrong")',
     "test_refusal_names_the_real_columns"),

    ("unscoped -- judges .sql and .md artifacts too",
     '    if not target_file.replace("\\\\", "/").endswith(".py"):\n        return ""\n',
     '    if False:\n        return ""\n',
     "test_ignores_non_py_targets"),
]


def controls() -> int:
    if not SRC.is_file():
        print("UNKNOWN: builder_mcp.py not found at %s" % SRC)
        return 2
    good = SRC.read_text(encoding="utf-8")
    bad_count = 0

    def run(node=None):
        target = TEST if node is None else f"{TEST}::{node}"
        return subprocess.run([sys.executable, "-m", "pytest", target, "-q"],
                              cwd=ROOT, capture_output=True, text=True, timeout=900)

    base = run()
    if base.returncode != 0:
        print("UNKNOWN: the suite is RED before any mutation -- controls cannot "
              "be interpreted.\n%s" % base.stdout[-1500:])
        return 2
    print("baseline: %s" % (base.stdout.strip().splitlines() or ["?"])[-1])

    for label, old, new, node in MUTATIONS:
        if old not in good:
            print("VACUOUS  %s\n         anchor not found -- the control cannot "
                  "fail, so it proves nothing" % label)
            bad_count += 1
            continue
        try:
            SRC.write_text(good.replace(old, new, 1), encoding="utf-8")
            r = run(node)
        finally:
            SRC.write_text(good, encoding="utf-8")
        if r.returncode != 0:
            print("RED      %s" % label)
        else:
            print("!!GREEN  %s\n         control did NOT go red -- vacuous "
                  "assertion" % label)
            bad_count += 1

    after = run()
    print("restored: %s" % (after.stdout.strip().splitlines() or ["?"])[-1])
    if after.returncode != 0:
        bad_count += 1
    print("\nVERDICT: %s" % ("all controls observed RED" if not bad_count
                             else "%d problem(s)" % bad_count))
    return 1 if bad_count else 0


# --------------------------------------------------------------------------
# --blast-radius
# --------------------------------------------------------------------------

def blast_radius(days: int, judge_json: str | None) -> int:
    rv = _load_rv()
    if rv is None:
        print("UNKNOWN: %s not found" % RV)
        return 2
    catalog, meta, unknown = rv.load_catalog()
    if unknown or not catalog:
        print("UNKNOWN: catalog unresolvable (%s) -- the gate would ALLOW every "
              "build and say so; this measurement cannot run." % unknown)
        return 2
    ns = _helpers(SRC.read_text(encoding="utf-8"))
    reject = ns.get("_reject_phantom_columns")
    if reject is None:
        print("UNKNOWN: _reject_phantom_columns not present in builder_mcp.py")
        return 2

    log = subprocess.run(["git", "log", f"--since={days}.days", "--diff-filter=A",
                          "--name-only", "--format=%H|%cs|%s"],
                         cwd=ROOT, capture_output=True, text=True, timeout=900).stdout
    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
    cur, adds = None, []
    for line in log.splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3 and len(parts[0]) == 40:
            cur = parts
        elif line.strip() and cur:
            adds.append((cur[0][:9], cur[1], cur[2], line.strip()))

    builder = [a for a in adds if a[3].endswith(".py")
               and a[2].lower().startswith(("build:", "scaffold"))]
    refused, flagged = [], set()
    for sha, date, _subj, rel in builder:
        p = ROOT / rel
        if not p.is_file():
            continue
        out = reject(rel, p.read_text(encoding="utf-8", errors="replace"),
                     catalog, rv._iter_sql_strings, rv.extract_refs)
        if out:
            refused.append((date, sha, rel, out))
            for ln in out.splitlines():
                ln = ln.strip()
                if ln and "--" in ln and "." in ln.split()[0]:
                    flagged.add(ln.split()[0])

    print("BASIS: ref=HEAD %s  window=%dd  planes=%s  bus_age=%.2fd"
          % (head, days, meta.get("planes"), meta.get("bus_age_days") or -1))
    print("  builder .py files ADDED in window .... %d" % len(builder))
    print("  ... that would be REFUSED ............ %d  (%.1f%%)"
          % (len(refused), 100.0 * len(refused) / max(1, len(builder))))
    print("  distinct phantom referents ........... %d" % len(flagged))
    for date, sha, rel, _out in sorted(refused):
        print("      %s %s  %s" % (date, sha, rel))

    rc = 0
    if judge_json:
        jp = Path(judge_json)
        if not jp.is_file():
            print("\nUNKNOWN: judge json %s not found -- agreement UNMEASURED, "
                  "which is not agreement." % jp)
            return 2
        judged = set(json.load(open(jp))["columns"]["missing"])
        fp = sorted(flagged - judged)
        print("\nAGREEMENT WITH referent_verify (%s):" % jp.name)
        print("  flagged here ......... %d" % len(flagged))
        print("  also flagged by judge  %d" % len(flagged & judged))
        print("  FALSE POSITIVES ...... %d %s" % (len(fp), fp))
        if fp:
            rc = 1
    else:
        print("\nagreement UNMEASURED (pass --judge-json artifacts/referent_verify.json)")
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--controls", action="store_true")
    ap.add_argument("--blast-radius", action="store_true")
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--judge-json", default=None)
    a = ap.parse_args()
    if not (a.controls or a.blast_radius):
        ap.error("pick --controls and/or --blast-radius")
    rc = 0
    if a.controls:
        rc = max(rc, controls())
    if a.blast_radius:
        rc = max(rc, blast_radius(a.days, a.judge_json))
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
