#!/usr/bin/env python3
"""Grade the STRANDED re-emissions (#4079) so the decision it owes can be made.

WHY THIS EXISTS
    cycle-0166 established on 2026-10-01 that the premise #4079 had been
    answered with three times was false: the re-emission already happened.
    46 of the 69 quarantined candidates are regenerated and sitting UNTRACKED
    on /home/workspace/zo_sentinel -- present on a disk, in no repository.
    #5922 made that bucket visible and deliberately left it NOT eligible.

    What nobody could answer cheaply is whether those 46 are worth landing.
    "46 machine-generated files nobody has reviewed" is not a decision input,
    so the issue has carried the same ask since 2026-08-26 and collected four
    re-derivations of a wall instead of one measurement of the output.

    This tool turns the bucket into a graded list. It lands nothing, emits
    nothing, deletes nothing, and writes nothing into the repo it reads.

WHAT IT GRADES, AND AGAINST WHAT
    The 69 were withdrawn (#4070) because they named PHANTOM TABLES -- tables
    that exist on no plane. So that is the thing to re-measure on the
    regenerated output, and it is measured with THIS repo's own referent
    instrument (tools/referent_verify.py: load_catalog, the SQL-string walk,
    extract_refs) rather than a second opinion written here. Two instruments
    that disagree about what a table is would be worse than none.

        CLEAN    parses, and every table it names exists on some plane
        PHANTOM  parses, and names >=1 table that exists on NO plane --
                 the regeneration reproduced the defect it was withdrawn for
        BROKEN   does not parse
        UNKNOWN  the catalog could not be loaded, so NOTHING was checked

    UNKNOWN IS NOT CLEAN. With no catalog every verdict is UNKNOWN and
    --enforce exits non-zero. R6: unknown is not zero -- and an empty catalog
    is not an absent one, which is pole 5 of the control below.

    "landed vs untracked" is likewise not re-derived here: it comes from
    requeue_quarantined.landed_state(), the one predicate that owns it since
    #5922. A second copy of that rule is how two tools come to disagree about
    whether work is done.

NEGATIVE CONTROL (R4) -- `--self-test`, and it is wired into CI
    A verdict is trusted only because its complement was observed:

      1. source naming a table on no plane            -> PHANTOM
      2. source naming a REAL table from the catalog  -> CLEAN
      3. source that does not parse                   -> BROKEN
      4. pole 2's source graded with the catalog ABSENT -> UNKNOWN
      5. pole 2's source graded with an EMPTY catalog and no reason -> PHANTOM

    Pole 4 is the one that matters: without it, CLEAN could mean "the check
    never ran". Pole 5 states why `unknown_reason` must be propagated rather
    than swallowed into `{}` -- swallow it and a missing catalog reports as 46
    phantom files, which is a false FAIL and gets the instrument switched off.

USAGE
    python tools/stranded_review.py --repo /home/workspace/zo_sentinel
    python tools/stranded_review.py --self-test            # the control
    python tools/stranded_review.py --json out.json --summary-md out.md
    python tools/stranded_review.py --enforce              # rc=1 on any
                                                           # PHANTOM/BROKEN/UNKNOWN
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve()
BAD_VERDICTS = ("PHANTOM", "BROKEN", "UNKNOWN")


# ------------------------------------------------------------- module load ---
def load_module(path: Path, name: str):
    """Import a sibling tool BY PATH, from the repo under review.

    sys.modules is populated BEFORE exec_module: a module that imports itself
    by name otherwise executes twice and the second copy's globals are not the
    ones the caller holds. (Same shape as the fu_ledger writer-door bite.)
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("cannot load %s from %s" % (name, path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------------ grading --
def grade_source(src: str, rv, catalog, unknown_reason):
    """(verdict, named_tables, missing_tables, detail) for one module's text."""
    try:
        tree = ast.parse(src)
    except SyntaxError as exc:
        return ("BROKEN", [], [],
                "SyntaxError line %s: %s" % (exc.lineno or 0, exc.msg))
    except (ValueError, RecursionError) as exc:                # noqa: BLE001
        return ("BROKEN", [], [], "%s: %s" % (type(exc).__name__, exc))

    named: set[str] = set()
    created: set[str] = set()
    for sql, _lineno in rv._iter_sql_strings(tree):
        clean = rv.strip_sql_comments(sql)
        for cm in rv.CREATED_TABLE.finditer(clean):
            created.add(cm.group(1).lower())
        tabs, _cols = rv.extract_refs(sql)
        named |= tabs

    if unknown_reason:
        # The check did not run. Saying CLEAN here is the majority class of
        # this ledger, and it is the only branch of this function that cannot
        # be distinguished from a pass by looking at the output alone.
        return ("UNKNOWN", sorted(named), [],
                "catalog unavailable (%s) -- nothing was checked" % unknown_reason)

    have = {str(t).lower() for t in catalog}
    missing = sorted(t for t in named if t not in have and t not in created)
    if missing:
        return ("PHANTOM", sorted(named), missing,
                "names %d table(s) on no plane: %s"
                % (len(missing), ", ".join(missing[:6])))
    return ("CLEAN", sorted(named), [],
            "%d table referent(s), all resolve" % len(named))


# ------------------------------------------------------------- self-test -----
def self_test(rv, catalog, unknown_reason) -> int:
    """Observe each verdict's complement. Returns 0 only if all poles hold."""
    if unknown_reason:
        print("SELF-TEST CANNOT RUN: catalog unavailable (%s)." % unknown_reason)
        print("  That is UNKNOWN, not a pass -- rc=2.")
        return 2
    if not catalog:
        print("SELF-TEST CANNOT RUN: catalog loaded but EMPTY -- rc=2.")
        return 2

    real = sorted(str(t) for t in catalog)[0]
    phantom = "no_such_table_c175_negative_control"
    sql_of = lambda t: 'QUERY = "SELECT id FROM %s WHERE x = 1"\n' % t

    poles = [
        ("1 phantom referent",      sql_of(phantom), catalog, None,
         "PHANTOM"),
        ("2 real referent",         sql_of(real),    catalog, None,
         "CLEAN"),
        ("3 unparseable source",    "def f(:\n",     catalog, None,
         "BROKEN"),
        ("4 real referent, catalog ABSENT", sql_of(real), {},
         "self-test: catalog withheld", "UNKNOWN"),
        ("5 real referent, catalog EMPTY and no reason",
         sql_of(real), {}, None, "PHANTOM"),
    ]

    ok = True
    print("NEGATIVE CONTROL -- %d pole(s). A verdict is evidence only because "
          "its complement was seen." % len(poles))
    print("  catalog: %d table(s); real referent used: %s" % (len(catalog), real))
    for label, src, cat, reason, want in poles:
        got, _named, _missing, detail = grade_source(src, rv, cat, reason)
        mark = "ok  " if got == want else "FAIL"
        if got != want:
            ok = False
        print("  %s %-46s want %-7s got %-7s  %s"
              % (mark, label, want, got, detail))
    print("SELF-TEST %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


# ----------------------------------------------------------------- basis -----
def git_head(repo: Path) -> str:
    try:
        r = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() or "UNKNOWN"
    except (OSError, subprocess.SubprocessError):
        return "UNKNOWN"


def newest_manifest(repo: Path) -> Path | None:
    found = sorted((repo / "quarantine").glob("QUARANTINE_*.json"))
    return found[-1] if found else None


# ------------------------------------------------------------------ main -----
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo", default=str(HERE.parents[1]),
                    help="repo to grade (default: the repo this tool lives in)")
    ap.add_argument("--manifest", default=None,
                    help="quarantine manifest (default: newest in quarantine/)")
    ap.add_argument("--tools-from", default=None,
                    help="where to import requeue_quarantined/referent_verify "
                         "from (default: <repo>/tools). Set when grading a repo "
                         "whose own copies are older than the predicate you want.")
    ap.add_argument("--json", dest="json_out", default=None)
    ap.add_argument("--summary-md", default=None)
    ap.add_argument("--enforce", action="store_true",
                    help="exit 1 if any stranded file is PHANTOM/BROKEN/UNKNOWN")
    ap.add_argument("--self-test", action="store_true",
                    help="run the negative control and exit")
    ap.add_argument("--all-states", action="store_true",
                    help="also grade landed and absent candidates")
    args = ap.parse_args(argv)

    repo = Path(args.repo).resolve()
    tools = Path(args.tools_from).resolve() if args.tools_from else repo / "tools"
    try:
        rq = load_module(tools / "requeue_quarantined.py", "requeue_quarantined")
        rv = load_module(tools / "referent_verify.py", "referent_verify")
    except Exception as exc:                                    # noqa: BLE001
        print("REFUSED: cannot import the repo's own instruments from %s (%s: %s)"
              % (tools, type(exc).__name__, exc))
        return 2
    if not hasattr(rq, "landed_state"):
        print("REFUSED: %s/requeue_quarantined.py has no landed_state(). That "
              "copy predates #5922 and evaluates landing with is_file(), which "
              "reads an untracked file as landed -- the #4079 defect itself."
              % tools)
        return 2

    catalog, cat_meta, cat_unknown = rv.load_catalog()
    if args.self_test:
        return self_test(rv, catalog, cat_unknown)

    man_path = Path(args.manifest) if args.manifest else newest_manifest(repo)
    if man_path is None or not man_path.is_file():
        print("REFUSED: no quarantine manifest found under %s/quarantine" % repo)
        return 2
    try:
        manifest = rq.load_manifest(man_path)
    except ValueError as exc:
        print("REFUSED: %s" % exc)
        return 2

    cands = list(manifest["re_emission"]["candidates"])
    by_file = {f.get("from"): f for f in (manifest.get("files") or [])
               if isinstance(f, dict)}

    rows, buckets = [], {"landed": 0, "untracked": 0, "absent": 0}
    for c in cands:
        state = rq.landed_state(repo, c)
        buckets[state] = buckets.get(state, 0) + 1
        row = {"candidate": c, "landed_state": state,
               "phantom_tables_when_withdrawn":
                   (by_file.get(c, {}) or {}).get("phantom_tables", []),
               "first_added_to_main":
                   (by_file.get(c, {}) or {}).get("first_added_to_main")}
        if state == "untracked" or args.all_states:
            p = repo / c
            if p.is_file():
                try:
                    src = p.read_text(encoding="utf-8", errors="replace")
                except OSError as exc:
                    row.update(verdict="UNKNOWN", detail="unreadable: %s" % exc,
                               named_tables=[], missing_tables=[], bytes=0)
                    rows.append(row)
                    continue
                verdict, named, missing, detail = grade_source(
                    src, rv, catalog, cat_unknown)
                row.update(verdict=verdict, detail=detail, named_tables=named,
                           missing_tables=missing, bytes=len(src.encode("utf-8")),
                           lines=src.count("\n") + 1)
            else:
                row.update(verdict="UNKNOWN", detail="file absent",
                           named_tables=[], missing_tables=[], bytes=0)
        rows.append(row)

    graded = [r for r in rows if r.get("verdict")]
    tally = {v: sum(1 for r in graded if r["verdict"] == v)
             for v in ("CLEAN", "PHANTOM", "BROKEN", "UNKNOWN")}

    basis = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": str(repo),
        "repo_head": git_head(repo),
        "tools_from": str(tools),
        "manifest": str(man_path),
        "catalog_planes": cat_meta.get("planes", []),
        "catalog_tables": len(catalog),
        "catalog_unknown_reason": cat_unknown,
        "bus_age_days": cat_meta.get("bus_age_days"),
    }

    print("=" * 72)
    print("STRANDED RE-EMISSION REVIEW -- #4079")
    print("=" * 72)
    for k in ("repo", "repo_head", "tools_from", "manifest", "catalog_planes",
              "catalog_tables", "bus_age_days", "generated_at"):
        print("  %-22s %s" % (k, basis[k]))
    if cat_unknown:
        print("  %-22s %s" % ("CATALOG UNKNOWN", cat_unknown))
    print()
    print("  candidates             %d" % len(cands))
    print("  landed (tracked)       %d" % buckets.get("landed", 0))
    print("  untracked (STRANDED)   %d" % buckets.get("untracked", 0))
    print("  absent                 %d" % buckets.get("absent", 0))
    print()
    print("  graded                 %d" % len(graded))
    for v in ("CLEAN", "PHANTOM", "BROKEN", "UNKNOWN"):
        print("    %-20s %d" % (v, tally[v]))
    print()

    order = {"PHANTOM": 0, "BROKEN": 1, "UNKNOWN": 2, "CLEAN": 3}
    for r in sorted(graded, key=lambda r: (order.get(r["verdict"], 9),
                                           r["candidate"])):
        print("  %-8s %-58s %s" % (r["verdict"], r["candidate"][:58],
                                   r["detail"][:70]))

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps({"basis": basis, "counts": buckets, "verdicts": tally,
                        "rows": rows}, indent=2), encoding="utf-8")
        print("\n  json -> %s" % args.json_out)

    if args.summary_md:
        md = ["| verdict | file | detail |", "|---|---|---|"]
        for r in sorted(graded, key=lambda r: (order.get(r["verdict"], 9),
                                               r["candidate"])):
            md.append("| `%s` | `%s` | %s |"
                      % (r["verdict"], r["candidate"], r["detail"]))
        Path(args.summary_md).write_text("\n".join(md) + "\n", encoding="utf-8")
        print("  md   -> %s" % args.summary_md)

    bad = sum(tally[v] for v in BAD_VERDICTS)
    if args.enforce and bad:
        print("\nENFORCING: %d stranded file(s) are not CLEAN -- rc=1" % bad)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
