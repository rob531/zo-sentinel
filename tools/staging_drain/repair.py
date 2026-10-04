#!/usr/bin/env python3
"""S5 -- repair: mechanical classes by script, everything else a builder directive.

Reads the ledger's `repair` rows (one per family: the newest version that fails
its gate). Two paths:

  --apply       run the in-repo repair scripts that fix a class deterministically
                (check_service_manifests --adopt --fix, repair_staged_model_names
                --apply, repair_staged_undefined_imports --apply), then report which
                staged directories changed so the caller re-censuses exactly those.
                Each script is idempotent and refuses to guess; a second run is a no-op.
  --emit DIR    write one builder directive per repair row that no script fixes.
                The directive names the service, quotes the gate failure VERBATIM,
                and its acceptance test is "this service passes the promotion gate".
                Shape = SENTINEL_DIRECTIVE_SCHEMA.md + the promoter's validator
                (task/handler/output_file/complexity/description). Missing files reuse
                tools/service_decomposer.decompose() so there is ONE emitter shape for
                a staged file; repairs of an existing file use the `patch_` prefix,
                which build_completion treats as edit-class (never reaped as a
                "redundant rebuild" because the output already exists).

A directive is skipped when a same-named .json already sits in directives/pending/,
directives/proposed/ or directives/ (incl. .done/.failed) -- the architect's own
dedup set. `--max-directives` caps a run (default 100): the chain runs daily and
drains the rest on later ticks; rows not yet emitted stay `repair` with
`directive: null` in the ledger, which report.py counts as remaining, not repairing.

    python tools/staging_drain/repair.py --apply
    python tools/staging_drain/repair.py --emit directives/pending --max-directives 100
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.dirname(HERE)
ROOT = os.path.dirname(TOOLS)
for p in (ROOT, TOOLS):
    if p not in sys.path:
        sys.path.insert(0, p)

DEFAULT_LEDGER = os.path.join(ROOT, "chairman", "staging_drain", "ledger.json")
DEFAULT_CENSUS = os.path.join(ROOT, "artifacts", "staging_drain", "census.json")
DIRECTIVES = os.path.join(ROOT, "directives")

MECHANICAL_SCRIPTS = [
    ("check_service_manifests", [sys.executable, os.path.join(TOOLS, "check_service_manifests.py"), "--adopt", "--fix"]),
    ("repair_staged_model_names", [sys.executable, os.path.join(TOOLS, "repair_staged_model_names.py"), "--apply"]),
    ("repair_staged_undefined_imports", [sys.executable, os.path.join(TOOLS, "repair_staged_undefined_imports.py"), "--apply"]),
]

ACCEPTANCE = ("ACCEPTANCE TEST: `python tools/promote_staged_to_active.py` reports [PROMOTE] for "
              "services/staged/{name} (service.toml valid, router.py exposes a router, "
              "`python -m services.staged.{name}.router` imports, `python -m services.staged."
              "{name}.contract` exits 0). Then `python tools/staging_drain/census.py --only {name} "
              "--merge artifacts/staging_drain/census.json` shows verdict PROMOTE.")

RULES = ("MUST: import the REAL data layer (app.db.get_session, app.models) -- never a mock, "
         "placeholder or in-memory stand-in (hollow rule, zo_sentinel/gates/hollow.py). "
         "MUST: write only files under services/staged/{name}/. "
         "MUST NOT: edit zo_sentinel/__init__.py, app/__init__.py, the Dockerfile, or any "
         "file outside the service directory. MUST NOT: import duckdb. "
         "MUST NOT: add a `_v2`/`_v3` sibling -- repair THIS directory.")


def _staged_changed_dirs():
    """Staged service dirs with uncommitted changes (git status), sorted."""
    try:
        out = subprocess.run(["git", "status", "--porcelain", "--", "services/staged"],
                             cwd=ROOT, capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    names = set()
    for line in out.splitlines():
        path = line[3:].strip().split(" -> ")[-1]
        m = re.match(r"services/staged/([^/]+)/", path)
        if m:
            names.add(m.group(1))
    return sorted(names)


def apply_mechanical(quiet=False):
    before = _staged_changed_dirs()
    results = []
    for label, cmd in MECHANICAL_SCRIPTS:
        t0 = time.time()
        try:
            proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=1800)
            tail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()[-3:]
            results.append({"script": label, "rc": proc.returncode, "seconds": round(time.time() - t0, 1),
                            "tail": tail})
        except (OSError, subprocess.TimeoutExpired) as exc:
            results.append({"script": label, "rc": None, "error": repr(exc)})
    after = _staged_changed_dirs()
    changed = sorted(set(after or []) - set(before or [])) if (before is not None and after is not None) else None
    if not quiet:
        for r in results:
            print("  %-34s rc=%s %s" % (r["script"], r.get("rc"), " | ".join(r.get("tail") or [r.get("error", "")])))
        print("  staged dirs changed by the scripts: %s" % ("UNKNOWN (git unavailable)" if changed is None else len(changed)))
    return {"scripts": results, "changed_dirs": changed}


# ---- directives -----------------------------------------------------------

def _in_flight(task):
    """True if the task already exists anywhere in the directive tree (the dedup set)."""
    for d in (DIRECTIVES, os.path.join(DIRECTIVES, "pending"), os.path.join(DIRECTIVES, "proposed")):
        if not os.path.isdir(d):
            continue
        for fn in os.listdir(d):
            stem = fn.replace(".done.json", "").replace(".failed.json", "").replace(".json", "")
            if stem == task:
                return True
    return False


def _missing_file_for(row, rec):
    """Which exemplar-mirrored file is missing, if the failure is a missing file."""
    fc = row.get("failure_class")
    files = set(rec.get("files") or [])
    if fc == "contract_missing" and "contract.py" not in files:
        return "contract.py"
    if fc == "import_module_not_found" and ".logic'" in (row.get("first_failure") or "") and "logic.py" not in files:
        return "logic.py"
    if fc in ("missing_router", "lib_no_router") and "router.py" not in files:
        return "router.py"
    return None


def _patch_target(row, rec):
    ff = row.get("first_failure") or ""
    m = re.search(r"services/staged/%s/([A-Za-z_]+\.py)" % re.escape(rec["service"]), ff)
    if m:
        return m.group(1)
    if row.get("failure_class") in ("missing_or_invalid_toml",):
        return "service.toml"
    return "router.py" if "router.py" in (rec.get("files") or []) else (rec.get("files") or ["router.py"])[0]


def build_directive(row, rec):
    name = rec["service"]
    staged = "services/staged/%s" % name
    missing = _missing_file_for(row, rec)
    if missing:
        try:
            from service_decomposer import decompose  # tools/ on sys.path
            spec = ("Repair of an existing staged service, not a new one. The directory already holds: %s. "
                    "Gate failure (verbatim): %s. %s %s" % (
                        ", ".join(rec.get("files") or []), row.get("first_failure") or row.get("failure_class"),
                        ACCEPTANCE.format(name=name), RULES.format(name=name)))
            for d in decompose(name, spec):
                if d["output_file"].endswith("/" + missing):
                    d["source"] = "staging_drain"
                    d["phase"] = "staging_drain.S5"
                    d["priority"] = 0.90 if row.get("vulnerability_family") else 0.85
                    d["summary"] = "%s: %s is missing; the gate holds it at '%s'" % (name, missing, row.get("failure_class"))
                    d["category"] = "staged_repair"
                    d["severity"] = "medium"
                    d["observed_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
                    d["evidence"] = {"gate": "tools/promote_staged_to_active.py", "first_failure": row.get("first_failure"),
                                     "failure_class": row.get("failure_class"), "ledger": "chairman/staging_drain/ledger.json"}
                    d["suggested_fix"] = "write %s/%s mirroring services/_exemplar/%s" % (staged, missing, missing)
                    d["details"] = {"family": row.get("family"), "version": row.get("version"),
                                    "runtime_class": row.get("runtime_class")}
                    return d
        except Exception as exc:  # fall through to the generic patch shape, visibly
            row["_decomposer_error"] = repr(exc)
    target = _patch_target(row, rec)
    task = "patch_%s_passes_gate" % name
    fc = row.get("failure_class") or "other"
    hint = {
        "import_model_name": "The name has NO referent in app/models.py (family B of tools/repair_staged_model_names.py): "
                             "either use the model that really exists for this data, or, if the table truly does not "
                             "exist, this service is unbuildable as specified -- say so in the PR and stop.",
        "contract_failed": "contract.py runs but fails: fix the ROUTE or the LOGIC so the contract's assertions hold "
                           "over the real data layer; do not weaken the contract to pass.",
        "hollow_member": "The named member has zero top-level statements. Give it a real body or delete it from the "
                         "service and fix every import of it.",
        "import_module_not_found": "The import names a module that is absent. If it is a third-party package, it is NOT "
                                   "in app/requirements.txt and cannot be added by this directive: rewrite without it. "
                                   "If it is an intra-service module, write it.",
        "import_syntax_error": "Fix the syntax error at the quoted location.",
        "import_other": "Fix the import-time failure quoted above.",
        "contract_timeout": "The contract hung for 120s. Remove any network call, server bind, or blocking loop from "
                            "import time and from the contract.",
    }.get(fc, "Fix the quoted gate failure.")
    desc = ("REPAIR staged service '%s' (%s; family %s v%d; runtime class %s) so it passes the promotion gate. "
            "GATE FAILURE (verbatim): %s. %s %s %s" % (
                name, staged, row.get("family"), row.get("version"), row.get("runtime_class"),
                row.get("first_failure") or fc, hint, ACCEPTANCE.format(name=name), RULES.format(name=name)))
    return {
        "task": task, "directive_id": task, "id": task,
        "handler": "generate_file",
        "output_file": "%s/%s" % (staged, target),
        "complexity": "medium" if fc in ("contract_failed", "import_model_name") else "low",
        "phase": "staging_drain.S5",
        "priority": 0.90 if row.get("vulnerability_family") else 0.85,
        "description": desc,
        "reads": ["%s/%s" % (staged, f) for f in (rec.get("files") or []) if f.endswith((".py", ".toml"))],
        "source": "staging_drain",
        "summary": "%s fails the promotion gate: %s" % (name, fc),
        "category": "staged_repair",
        "severity": "medium",
        "observed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "evidence": {"gate": "tools/promote_staged_to_active.py", "first_failure": row.get("first_failure"),
                     "failure_class": fc, "ledger": "chairman/staging_drain/ledger.json"},
        "suggested_fix": hint,
        "details": {"family": row.get("family"), "version": row.get("version"), "runtime_class": row.get("runtime_class"),
                    "files": rec.get("files")},
    }


def _mechanical_scripts_changed_nothing(census_path):
    """True when this tick's --apply ran and changed no staged dir (repair_apply.json).
    Then a 'mechanical' row is mechanical in name only -- the scripts refused it
    (family B model names, undefined names with no single provenance) -- and it
    needs a directive like any other. Unknown (no apply record) -> False."""
    p = os.path.join(os.path.dirname(os.path.abspath(census_path)), "repair_apply.json")
    try:
        with open(p, encoding="utf-8") as fh:
            rec = json.load(fh)
    except (OSError, ValueError):
        return False
    return rec.get("changed_dirs") == []


def emit_directives(ledger, census, out_dir, max_directives=100, quiet=False, include_mechanical=False):
    recs = {r["service"]: r for r in census.get("services", [])}
    rows = []
    for n, r in ledger["services"].items():
        if r["outcome"] != "repair":
            continue
        mech = str(r.get("repair_path", "")).startswith("mechanical")
        if mech and not include_mechanical:
            continue
        if mech:
            r["repair_path"] += " -- script ran and changed nothing; builder directive"
        rows.append((n, r))
    # vulnerability families first (they unblock CVE coverage and the ASK index), then by name
    rows.sort(key=lambda nr: (not nr[1].get("vulnerability_family"), nr[0]))
    os.makedirs(out_dir, exist_ok=True)
    written, skipped, pending_rows = [], [], []
    for name, row in rows:
        rec = recs.get(name)
        if rec is None:
            continue
        d = build_directive(row, rec)
        if _in_flight(d["task"]):
            skipped.append((name, d["task"]))
            row["directive"] = d["task"] + " (already in flight)"
            continue
        if len(written) >= max_directives:
            pending_rows.append(name)
            row["directive"] = None
            continue
        path = os.path.join(out_dir, d["task"] + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(d, fh, indent=2)
        row["directive"] = os.path.relpath(path, ROOT).replace(os.sep, "/")
        written.append(path)
    if not quiet:
        print("  directives written: %d  already in flight: %d  deferred by cap: %d  (mechanical rows: %d)"
              % (len(written), len(skipped), len(pending_rows),
                 sum(1 for r in ledger["services"].values()
                     if r["outcome"] == "repair" and str(r.get("repair_path", "")).startswith("mechanical")
                     and not include_mechanical)))
    return {"written": [os.path.relpath(p, ROOT) for p in written], "in_flight": skipped, "deferred": pending_rows}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--census", default=DEFAULT_CENSUS)
    ap.add_argument("--apply", action="store_true", help="run the mechanical repair scripts")
    ap.add_argument("--emit", metavar="DIR", help="write builder directives into DIR")
    ap.add_argument("--max-directives", type=int, default=100)
    ap.add_argument("--include-mechanical", action="store_true",
                    help="also emit for rows routed to a script (automatic when --apply changed nothing)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if not (args.apply or args.emit):
        ap.error("nothing to do: pass --apply and/or --emit DIR")
    rc = 0
    if args.apply:
        if not args.quiet:
            print("=== staging_drain S5 mechanical repairs ===")
        res = apply_mechanical(quiet=args.quiet)
        with open(os.path.join(os.path.dirname(args.census), "repair_apply.json"), "w", encoding="utf-8") as fh:
            json.dump(res, fh, indent=1)
        if any(r.get("rc") not in (0, 1) for r in res["scripts"]):
            rc = 1
    if args.emit:
        if not (os.path.isfile(args.ledger) and os.path.isfile(args.census)):
            print("UNKNOWN: ledger or census missing -- run census.py then classify.py")
            return 2
        with open(args.ledger, encoding="utf-8") as fh:
            ledger = json.load(fh)
        with open(args.census, encoding="utf-8") as fh:
            census = json.load(fh)
        if not args.quiet:
            print("=== staging_drain S5 directives -> %s ===" % args.emit)
        inc = args.include_mechanical or _mechanical_scripts_changed_nothing(args.census)
        res = emit_directives(ledger, census, args.emit, args.max_directives, quiet=args.quiet,
                              include_mechanical=inc)
        ledger["directives_emitted_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with open(args.ledger, "w", encoding="utf-8") as fh:
            json.dump(ledger, fh, indent=1)
    return rc


if __name__ == "__main__":
    sys.exit(main())
