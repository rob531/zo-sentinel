#!/usr/bin/env python3
"""retire_triage.py -- derive the RETIRE recommendation for deferred routers IN CODE.

Why this exists (chairman issue #4004, improvement-loop cycle-0165)
-------------------------------------------------------------------
#4004 asked the chairman to approve 12 RETIRE deletions. That list of 12 was
never produced by a program. It lives as a 63-row markdown table in
`docs/G4_REACHABILITY_ANALYSIS.md`; `tools/orphanage.py` emits
MOUNTABLE / BROKEN_IMPORT / NO_ROUTES / EDIT_CLASS / ... and has never emitted
RETIRE at all. Eleven of the twelve stood on ONE sentence in that table:

    "ALSO duplicated at services/staged/<stem>, so the promotion lane is its
     real path"

The chairman review of 2026-09-21 re-derived it live and found that premise
FALSE for 7 of them -- 4 staged dirs are manifest-only stubs (a `service.toml`
and nothing else) and 3 declare no routes -- and `services/active/<stem>/`
existed for NONE of the 12. Verdict: 1 recommended, 11 REJECTED, and the
standing ruling is that the 11 "may be re-proposed ... as individually-evidenced
deletions -- NEVER AGAIN AS A CLASS CLEARED BY ONE OVERRIDE."

That correction was made by hand against a markdown table, so nothing stopped
the next triage from re-deriving the same defective list: the instrument was a
paragraph. This file is the instrument. There is deliberately NO class-wide
override in it -- every RETIRE must satisfy every predicate of one of the two
enumerated paths on its own evidence, and a withheld RETIRE prints the
predicate that failed and the measurement that failed it.

It DELETES NOTHING and RETIRES NOTHING (`data_deletion` is FOREVER_HELD, and
#4004's ask 1 is an explicit chairman reservation). It emits the evidence the
decision needs.

    python tools/retire_triage.py                 # live per-deferral triage
    python tools/retire_triage.py --json
    python tools/retire_triage.py --emit-md docs/G4_RETIRE_TRIAGE_LIVE.md
    python tools/retire_triage.py --check-doc docs/G4_REACHABILITY_ANALYSIS.md

`--check-doc` is the latch: rc=1 when a markdown table asserts RETIRE for a
stem whose premise does not hold against live state. It is RED on
docs/G4_REACHABILITY_ANALYSIS.md today, by construction -- that is the
observation, not a regression.

Route counting is delegated to `reachability_ratchet.describe()` ON PURPOSE.
A second counter in this file is how `[^"']+` vs `[^"']*` (PR #5375) would be
re-introduced on a surface nobody re-tests.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import reachability_ratchet as RR  # noqa: E402

STAGED = os.path.join(ROOT, "services", "staged")
ACTIVE = os.path.join(ROOT, "services", "active")
DEFERRED = os.path.join(ROOT, "tools", "reachability_deferred.json")

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
             "ci-venv", ".ci-venv", ".mypy_cache", ".pytest_cache", "site-packages"}

# The two -- and only two -- ways a deferred root router can earn RETIRE.
# Each is a conjunction: every predicate must hold on that module's own
# evidence. No path is satisfied by a sentence about a sibling directory.
PATH_DEAD = ("zero_routes", "zero_importers", "no_staged_dir", "no_active_dir")
PATH_PROMOTED = ("active_dir_exists", "active_covers_root_routes", "zero_importers")

PATH_BASIS = {
    "DEAD": "declares no routes, nothing imports it, and there is no staged and "
            "no active counterpart -- nothing is superseded because nothing is there",
    "PROMOTED": "services/active/<stem>/ exists and serves every route the root "
                "copy declares -- the promotion actually happened",
}


def _read(path):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def deferred_stems():
    doc = json.loads(_read(DEFERRED) or "{}")
    return sorted((doc.get("deferred") or {}).keys())


def _dir_probe(base, stem):
    """Classify a services/<tier>/<stem>/ directory -- the object the #4004
    override ASSERTED existed as a promotion lane, measured instead.

    ABSENT         no directory at all
    MANIFEST_ONLY  a service.toml (or other non-.py) and nothing executable
    NO_ROUTES      .py files exist but declare zero routes
    REAL           .py files declaring >= 1 route
    """
    d = os.path.join(base, stem)
    if not os.path.isdir(d):
        return {"state": "ABSENT", "files": [], "py_files": [],
                "route_count": 0, "routes": [], "present": False}
    files, pys, routes = [], [], []
    for dirpath, dirs, names in os.walk(d):
        dirs[:] = [x for x in dirs if x not in SKIP_DIRS]
        for n in sorted(names):
            rel = os.path.relpath(os.path.join(dirpath, n), d).replace("\\", "/")
            files.append(rel)
            if n.endswith(".py"):
                pys.append(rel)
                routes.extend(RR.describe(n, _read(os.path.join(dirpath, n)))["routes"])
    state = "MANIFEST_ONLY" if not pys else ("NO_ROUTES" if not routes else "REAL")
    return {"state": state, "files": sorted(files), "py_files": pys,
            "route_count": len(routes), "routes": routes, "present": True}


_PY_INDEX = None


def _py_index():
    """One walk, reused for all stems: rel path -> source."""
    global _PY_INDEX
    if _PY_INDEX is None:
        idx = {}
        for dirpath, dirs, names in os.walk(ROOT):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            for n in names:
                if n.endswith(".py"):
                    p = os.path.join(dirpath, n)
                    idx[os.path.relpath(p, ROOT).replace("\\", "/")] = _read(p)
        _PY_INDEX = idx
    return _PY_INDEX


def importers(stem):
    """Files that actually import the root module.

    R6: an empty result is 'no importer found by these two patterns', which is
    why both the static and the importlib form are searched and the hit list is
    published rather than summarised to a count.
    """
    static = re.compile(r"(?:^|\n)[ \t]*(?:from[ \t]+%s[ \t]+import|import[ \t]+%s(?=[ \t,.\n]|$))"
                        % (re.escape(stem), re.escape(stem)))
    dynamic = re.compile(r"import_module\([ \t]*[\"']%s[\"']" % re.escape(stem))
    hits = []
    for rel, src in _py_index().items():
        if rel == stem + ".py":
            continue
        if static.search(src) or dynamic.search(src):
            hits.append(rel)
    return sorted(hits)


def triage_one(stem):
    root_py = os.path.join(ROOT, stem + ".py")
    exists = os.path.isfile(root_py)
    shape = RR.describe(stem + ".py", _read(root_py)) if exists else None
    routes = (shape or {}).get("routes", [])
    staged = _dir_probe(STAGED, stem)
    active = _dir_probe(ACTIVE, stem)
    imps = importers(stem)

    ev = {
        "zero_routes": len(routes) == 0,
        "zero_importers": not imps,
        "no_staged_dir": staged["state"] == "ABSENT",
        "no_active_dir": not active["present"],
        "active_dir_exists": active["present"],
        "active_covers_root_routes": active["present"]
                                     and set(routes) <= set(active["routes"]),
    }

    basis, failed = None, {}
    for name, preds in (("DEAD", PATH_DEAD), ("PROMOTED", PATH_PROMOTED)):
        bad = [p for p in preds if not ev[p]]
        if not bad:
            basis = name
            break
        failed[name] = bad

    rec = {
        "module": stem,
        "root_module_present": exists,
        "verdict": "RETIRE" if basis else "HOLD",
        "basis": basis,
        "basis_text": PATH_BASIS.get(basis) if basis else None,
        "live_route_count": len(routes),
        "live_routes": routes,
        "importers": imps,
        "staged": {k: staged[k] for k in ("state", "files", "route_count")},
        "active": {k: active[k] for k in ("state", "files", "route_count")},
        "failed_predicates": failed,
        "evidence": evidence_lines(stem, exists, routes, imps, staged, active),
    }
    return rec


def evidence_lines(stem, exists, routes, imps, staged, active):
    """One line per measured fact. This is what 'individually-evidenced' means:
    the reader can refute any single line without re-running the tool."""
    out = []
    if not exists:
        out.append("%s.py does not exist at the repo root -- nothing to retire" % stem)
        return out
    out.append("root %s.py declares %d route(s)%s"
               % (stem, len(routes),
                  (": " + ", ".join(routes[:6])) if routes else ""))
    out.append("importers: %s" % (", ".join(imps) if imps else "none found "
               "(static `import`/`from ... import` and importlib.import_module)"))
    for tier, probe in (("staged", staged), ("active", active)):
        if probe["state"] == "ABSENT":
            out.append("services/%s/%s/ does not exist" % (tier, stem))
        elif probe["state"] == "MANIFEST_ONLY":
            out.append("services/%s/%s/ is a MANIFEST-ONLY STUB (%s) -- no .py, "
                       "so it is not a promotion lane"
                       % (tier, stem, ", ".join(probe["files"]) or "empty"))
        elif probe["state"] == "NO_ROUTES":
            out.append("services/%s/%s/ has .py (%s) but declares ZERO routes -- "
                       "the lane leads nowhere"
                       % (tier, stem, ", ".join(probe["py_files"])))
        else:
            out.append("services/%s/%s/ declares %d route(s) -- a real lane"
                       % (tier, stem, probe["route_count"]))
    return out


DOC_ROW = re.compile(
    r"^\|[^|]*\|\s*`?([A-Za-z_][A-Za-z0-9_]*)`?\s*\|\s*\*{0,2}RETIRE\*{0,2}\s*\|",
    re.M)


def doc_retire_claims(path):
    """Stems a markdown table asserts RETIRE for. Returns [] for a missing file,
    and the caller treats that as rc=2 -- an unreadable table is not agreement."""
    src = _read(path)
    if not src:
        return None
    return sorted(set(DOC_ROW.findall(src)))


def render_md(recs):
    lines = [
        "# G4 RETIRE triage -- DERIVED, not hand-written",
        "",
        "Generated by `tools/retire_triage.py` from live repo state. Do not edit:",
        "re-run the tool. The hand-written 63-row table in",
        "`docs/G4_REACHABILITY_ANALYSIS.md` cleared 11 modules as a class on one",
        "override sentence and was rejected by the chairman review of 2026-09-21;",
        "every row below carries its own predicates.",
        "",
        "| module | verdict | basis | live routes | importers | staged | active |",
        "|---|---|---|---:|---:|---|---|",
    ]
    for r in recs:
        lines.append("| `%s` | **%s** | %s | %d | %d | %s | %s |" % (
            r["module"], r["verdict"], r["basis"] or "--",
            r["live_route_count"], len(r["importers"]),
            r["staged"]["state"], r["active"]["state"]))
    n_ret = sum(1 for r in recs if r["verdict"] == "RETIRE")
    lines += ["", "**%d of %d deferrals earn RETIRE on their own evidence.**"
              % (n_ret, len(recs)), ""]
    for r in recs:
        lines += ["### `%s` -- %s" % (r["module"], r["verdict"]), ""]
        lines += ["- %s" % e for e in r["evidence"]]
        if r["verdict"] == "HOLD":
            for pathname, bad in sorted(r["failed_predicates"].items()):
                lines.append("- path %s fails: %s" % (pathname, ", ".join(bad)))
        lines.append("")
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Per-deferral RETIRE triage derived from live state. "
                    "Deletes nothing; emits the evidence a deletion decision needs.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", action="append", default=[],
                    help="restrict to this stem (repeatable)")
    ap.add_argument("--emit-md", metavar="PATH",
                    help="write the derived triage table to PATH")
    ap.add_argument("--check-doc", metavar="PATH",
                    help="rc=1 if PATH asserts RETIRE for a stem that is HOLD "
                         "live; rc=2 if PATH cannot be read (never a pass)")
    args = ap.parse_args(argv)

    stems = args.only or deferred_stems()
    recs = [triage_one(s) for s in stems]

    if args.emit_md:
        with open(args.emit_md, "w", encoding="utf-8") as fh:
            fh.write(render_md(recs))

    if args.check_doc:
        claims = doc_retire_claims(args.check_doc)
        if claims is None:
            print("CANNOT READ %s -- unknown is not agreement (R6)" % args.check_doc)
            return 2
        live = {r["module"]: r for r in recs}
        unsupported, unknown = [], []
        for stem in claims:
            r = live.get(stem)
            if r is None:
                unknown.append(stem)
            elif r["verdict"] != "RETIRE":
                unsupported.append(r)
        print("=== %s asserts RETIRE for %d stem(s); %d are deferred today ==="
              % (args.check_doc, len(claims), len(claims) - len(unknown)))
        for r in unsupported:
            print("  UNSUPPORTED  %-44s %s" % (r["module"], r["evidence"][0]))
            for e in r["evidence"][1:]:
                print("               %-44s %s" % ("", e))
        if unknown:
            print("  NOT DEFERRED (not evaluated here): %s" % ", ".join(unknown))
        if unsupported:
            print("\nrc=1: %d of %d RETIRE assertion(s) do not hold against live "
                  "state." % (len(unsupported), len(claims)))
            return 1
        print("\nrc=0: every RETIRE assertion in that table holds live.")
        return 0

    if args.json:
        print(json.dumps({"deferred_count": len(recs), "triage": recs}, indent=2))
        return 0

    n_ret = sum(1 for r in recs if r["verdict"] == "RETIRE")
    print("=== RETIRE triage: %d deferral(s), %d earn RETIRE on own evidence ==="
          % (len(recs), n_ret))
    print("  basis: live repo state under %s" % ROOT)
    for r in recs:
        print("\n  [%-6s] %-46s routes=%d importers=%d staged=%-13s active=%s"
              % (r["verdict"], r["module"], r["live_route_count"],
                 len(r["importers"]), r["staged"]["state"], r["active"]["state"]))
        if r["basis"]:
            print("           basis %s: %s" % (r["basis"], r["basis_text"]))
        for e in r["evidence"]:
            print("           - %s" % e)
        for pathname, bad in sorted(r["failed_predicates"].items()):
            print("           path %s fails: %s" % (pathname, ", ".join(bad)))
    print("\n  NOTHING WAS DELETED. data_deletion is FOREVER_HELD and #4004 ask 1 "
          "is an explicit chairman reservation.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
