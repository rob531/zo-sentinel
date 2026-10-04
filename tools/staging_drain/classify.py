#!/usr/bin/env python3
"""S3 + S6 -- turn the census into the outcome ledger: one outcome per staged service.

Outcomes (the chain's exit condition says exactly one of the first four is true for
every staged directory; anything else is `remaining`, and is reported as such):

  promoted     live in prod. ONLY from promotions.json, which S4 writes with the
               prod evidence (deploy id + the probe output). The gate alone never
               makes a service "promoted" -- on 2026-09-22 every static gate was
               green and the image could not boot.
  superseded   a newer version of the same family passed (or is the repair target),
               or an ACTIVE service already owns the name / the family at >= this
               version / the declared route. `superseded_by` names it.
  repair       the family's repair target: source exists, the gate fails. S5 turns
               this row into a mechanical fix or a builder directive.
  retired      no source to repair. The reason is recorded; nothing is deleted.
  promotable   passes its gate, awaiting S4 (prod evidence). Counted as remaining.

Deterministic: the same census + promotions.json always yields the same ledger.
The ledger is the ONE writer for "why is this service still staged" (LOCO_CHAIRMAN
§11.3); the daily line and the exclusion file are derived from it.

    python tools/staging_drain/classify.py                      # census -> ledger
    python tools/staging_drain/classify.py --census X --ledger Y --promotions Z
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
DEFAULT_CENSUS = os.path.join(ROOT, "artifacts", "staging_drain", "census.json")
DEFAULT_LEDGER = os.path.join(ROOT, "chairman", "staging_drain", "ledger.json")
DEFAULT_PROMOTIONS = os.path.join(ROOT, "chairman", "staging_drain", "promotions.json")
ACTIVE = os.path.join(ROOT, "services", "active")

VERSION_RE = re.compile(r"^(?P<family>.+?)_v(?P<ver>\d+)$")
VULN_RE = re.compile(r"(cve|vuln|nvd|ghsa|osv|advisor)", re.I)

# S5 routing: which gate failures an existing in-repo script repairs by itself.
MECHANICAL = {
    "missing_or_invalid_toml": "python tools/check_service_manifests.py --adopt --fix",
    "import_model_name": "python tools/repair_staged_model_names.py --apply   (family A only; family B = no referent -> directive)",
    "import_undefined_name": "python tools/repair_staged_undefined_imports.py --apply",
}

NO_SOURCE_REASON = (
    "no source in this tree (files: {files}); the builder's per-file scaffold stopped at "
    "{stage} -- service_decomposer lands __init__/service.toml by write_raw while router/"
    "logic/contract come from the engine (~21%% landing, c122). A copy may exist on the "
    "build host and never have been published (tools/staged_repo_reconcile.py); from this "
    "tree there is nothing to repair, so it is retired under the 2026-10-03 direction. "
    "Nothing is deleted; a later census that finds source re-classifies it."
)


def split_family(name):
    m = VERSION_RE.match(name)
    return (m.group("family"), int(m.group("ver"))) if m else (name, 1)


def load_json(path, default):
    if not path or not os.path.isfile(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def active_index(active_dir=ACTIVE):
    """{family: [(version, name)]} for every ACTIVE service dir."""
    idx = {}
    if not os.path.isdir(active_dir):
        return idx
    for n in sorted(os.listdir(active_dir)):
        if n.startswith((".", "_")) or not os.path.isdir(os.path.join(active_dir, n)):
            continue
        fam, ver = split_family(n)
        idx.setdefault(fam, []).append((ver, n))
    return idx


def _verdict(rec):
    g = rec.get("gate") or {}
    return g.get("verdict", "unmeasured")


def _passes(rec):
    """The gate that applies to this runtime class is green.

    api    -> the promoter's router gate.
    worker -> every source module imports AND an entrypoint exists (census evidence).
    lib    -> never on its own. A lib "ships with whatever imports it"; a staged dir
              holding only logic.py with no router and no entrypoint is an api
              scaffold the builder never finished (the exemplar is router+logic+
              contract), so it is a REPAIR target (missing router), not a candidate.
              On 2026-10-04 the first draft called 167 of these "promotable".
    """
    if rec.get("runtime_class_observed") == "api":
        return _verdict(rec) == "PROMOTE"
    if rec.get("runtime_class_observed") == "worker":
        return rec.get("source_import_ok") is True
    return False


def _gate_name(rec):
    rc = rec.get("runtime_class_observed")
    if rc == "api":
        return "promote_staged_to_active (router gate: toml, router, import, contract)"
    if rc == "worker":
        return "source import (every source module imports in a clean interpreter) + entrypoint; no zo scheduled-job gate exists yet"
    if rc == "lib":
        return "promote_staged_to_active (router gate) -- a lib alone has no gate to pass"
    return "none"


def _failure(rec):
    """First failure line for the gate that applies to this runtime class."""
    rc = rec.get("runtime_class_observed")
    if rc == "api":
        return rec.get("first_failure") or "", rec.get("failure_class") or "other"
    if rc in ("worker", "lib"):
        si = rec.get("source_import") or {}
        bad = [(m, r["detail"]) for m, r in si.items() if not r.get("ok")]
        if bad:
            m, d = bad[0]
            return "source import FAILED: services.staged.%s.%s: %s" % (rec["service"], m, d), _import_class(d)
        if rc == "lib":
            return ("router.py exposes no router -- %s imports but nothing serves it and it has no "
                    "entrypoint: an api scaffold stopped at logic.py" % ", ".join(rec.get("source_modules") or []),
                    "lib_no_router")
        return "", "none"
    return rec.get("first_failure") or "", rec.get("failure_class") or "other"


def _import_class(detail):
    d = detail or ""
    if "cannot import name" in d:
        return "import_model_name"
    if "No module named" in d:
        return "import_module_not_found"
    if "NameError" in d:
        return "import_undefined_name"
    if "SyntaxError" in d or "IndentationError" in d:
        return "import_syntax_error"
    return "import_other"


def classify(census, promotions=None, active=None):
    """Return the ledger dict. Pure function of its inputs."""
    recs = {r["service"]: r for r in census.get("services", [])}
    promotions = promotions or {}
    active = active if active is not None else active_index()
    out = {}

    # 1. promoted: sticky, evidence-bearing, written by S4 only.
    for name, rec in recs.items():
        ev = promotions.get(name)
        if ev and ev.get("evidence"):
            out[name] = {"outcome": "promoted", "reason": "live in prod: %s" % ev.get("summary", ""),
                         "evidence": ev["evidence"], "promoted_at": ev.get("at")}

    # 2. retired: no source.
    for name, rec in recs.items():
        if name in out:
            continue
        if rec.get("has_source") is False:
            out[name] = {"outcome": "retired",
                         "reason": NO_SOURCE_REASON.format(files=", ".join(rec.get("files") or []) or "none",
                                                           stage=rec.get("scaffold_stage"))}
        elif rec.get("has_source") is None:
            out[name] = {"outcome": "remaining", "reason": "census error: %s" % rec.get("census_error")}

    # 3. superseded by ACTIVE (name, family version, or route owner).
    for name, rec in recs.items():
        if name in out:
            continue
        fam, ver = split_family(name)
        if fam in active:
            best_ver, best_name = max(active[fam])
            if best_ver >= ver:
                out[name] = {"outcome": "superseded", "superseded_by": "active:%s" % best_name,
                             "reason": "an active service already carries family %r at v%d (this is v%d)"
                                       % (fam, best_ver, ver)}
                continue
        fc = rec.get("failure_class")
        ff = rec.get("first_failure") or ""
        if fc == "name_already_active":
            out[name] = {"outcome": "superseded", "superseded_by": "active:%s" % name, "reason": ff}
        elif fc == "route_collision":
            m = re.search(r"'([^']+)'\}", ff)
            owner = m.group(1) if m else "unknown"
            out[name] = {"outcome": "superseded", "superseded_by": "active:%s" % owner,
                         "reason": ff + " -- the active service owns the route; this staged copy is a permutation of it"}

    # 4. within-family dedupe among the rest.
    fams = {}
    for name, rec in recs.items():
        if name in out:
            continue
        fams.setdefault(rec["family"], []).append(rec)
    for fam, members in fams.items():
        members.sort(key=lambda r: (-r["version"], r["service"]))
        passing = [r for r in members if _passes(r)]
        keeper = passing[0] if passing else members[0]
        for r in members:
            if r is keeper:
                continue
            out[r["service"]] = {
                "outcome": "superseded", "superseded_by": keeper["service"],
                "reason": "family %r: %s v%d is the %s" % (
                    fam, keeper["service"], keeper["version"],
                    "newest version that passes the gate" if passing else "newest version and therefore the repair target"),
            }
        if _passes(keeper):
            out[keeper["service"]] = {
                "outcome": "promotable",
                "reason": "passes %s; awaiting S4 (%s) with prod evidence" % (
                    _gate_name(keeper),
                    "Fly image + boot test" if keeper["runtime_class_observed"] == "api" else "zo scheduled job"),
            }
        else:
            ff, fc = _failure(keeper)
            mech = MECHANICAL.get(fc)
            out[keeper["service"]] = {
                "outcome": "repair", "failure_class": fc, "first_failure": ff[:600],
                "repair_path": "mechanical: %s" % mech if mech else "builder directive",
                "reason": "fails %s: %s" % (_gate_name(keeper), ff[:300] or fc),
            }

    # decorate every row with the census facts the one-pager and S5 need
    for name, row in out.items():
        rec = recs[name]
        row.update({
            "family": rec["family"], "version": rec["version"],
            "runtime_class": rec.get("runtime_class_observed"),
            "scaffold_stage": rec.get("scaffold_stage"),
            "gate_verdict": _verdict(rec),
            "vulnerability_family": bool(VULN_RE.search(name)),
            "footprint_delta_kb": rec.get("footprint_delta_kb"),
        })
    counts = {}
    for row in out.values():
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "writer": "tools/staging_drain/classify.py",
        "basis": {"census_generated_at": census.get("generated_at"),
                  "census_git_head": (census.get("basis") or {}).get("git_head"),
                  "census_mode": (census.get("basis") or {}).get("mode"),
                  "active_families": len(active)},
        "counts": counts,
        "services": dict(sorted(out.items())),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--census", default=DEFAULT_CENSUS)
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--promotions", default=DEFAULT_PROMOTIONS)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    census = load_json(args.census, None)
    if census is None:
        print("UNKNOWN: no census at %s -- run census.py first" % args.census)
        return 2
    if (census.get("basis") or {}).get("mode") != "full-gate":
        print("REFUSED: census mode is %r, not full-gate -- a ledger from an unmeasured gate is a bucket label"
              % (census.get("basis") or {}).get("mode"))
        return 2
    promotions = load_json(args.promotions, {}).get("services", {}) if os.path.isfile(args.promotions) else {}
    ledger = classify(census, promotions)
    os.makedirs(os.path.dirname(os.path.abspath(args.ledger)), exist_ok=True)
    with open(args.ledger, "w", encoding="utf-8") as fh:
        json.dump(ledger, fh, indent=1)
    if not args.quiet:
        print("=== staging_drain S3/S6 ledger ===")
        print(json.dumps(ledger["counts"], indent=2))
        print("written: %s" % args.ledger)
    return 0


if __name__ == "__main__":
    sys.exit(main())
