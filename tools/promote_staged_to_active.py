#!/usr/bin/env python3
"""promote_staged_to_active.py -- the staged -> active promotion gate.

SOA step 4 (design 2026-07-21). Operating model = a RECURSIVE self-building
loop: the review is the automated gate chain (liveness contract + route-collision +
model cross-review), NOT a human. observe->enforce is the loop's own confidence
ramp (auto-promote on green), not a human sign-off. This is the reachability decision AND the harness-engineering
correctness linter the whole line of work needs: the entire failure history is
gates that prove PRESENCE, never CORRECTNESS (reachability postmortem;
Harness-Engineering doctrine, GOOSE_WATCH). A folder move that only checked "does
a file exist" would rebuild the invisibility bug in SOA costume. So this gate
proves the service actually WORKS before it becomes reachable:

  1. service.toml present + valid (name + import_path)                [static]
  2. router.py exposes a router                                       [static]
  3. LIVENESS: `python -m services.staged.<name>.contract` exits 0    [SUBPROCESS]
       -- boots, mounts, serves 200, schema-valid body. FAIL LOUD: a contract
          that cannot even RUN is a HOLD, never a silent pass (the inverse of
          FU-031's 74% Tier-0 degradation).
  4. route collision: declared routes do not clash with an ACTIVE
     service's routes                                                 [static]
  5. near-dup: name not already active                                [static]

A service that fails any gate stays in staged/ -- visible, counted, un-live,
costing nothing: an explicit staging area with a defined exit, not an accident.

MODES (mirror the reachability ratchet: observe first, enforce later)
  observe (default): report each candidate's verdict; MOVE NOTHING.
  --enforce         : os.rename staged/<name> -> active/<name> for PROMOTE
                      verdicts, capped by --max-per-run (human-gated first cohort).
  --regenerate      : after an enforced promotion, run generate_spine --emit so
                      app/_spine_generated.py reflects the newly-active service.

    python tools/promote_staged_to_active.py                 # observe (report only)
    python tools/promote_staged_to_active.py --enforce --max-per-run 1 --regenerate
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # tools/ -> linter importable
import model_import_linter as _linter  # noqa: E402  FU-031 harness linter
STAGED = os.path.join(ROOT, "services", "staged")
ACTIVE = os.path.join(ROOT, "services", "active")
ARTIFACT = os.path.join(ROOT, "artifacts", "staged_promotion_report.json")
DOCKERFILE = os.path.join(ROOT, "Dockerfile")

# FU-031 scar: this script is run as `python tools/promote_staged_to_active.py`,
# so sys.path[0] is tools/, NOT the repo root -- `import tools.x` fails there.
# Accept both invocation shapes rather than assuming one.
try:  # repo root on sys.path (pytest, `python -m tools....`)
    from tools.image_ship_check import would_be_shipped  # noqa: E402
except ImportError:  # script-dir sys.path[0]
    from image_ship_check import would_be_shipped  # noqa: E402

# FU-236 seam 4 -- the SAME rule object the other three seams use. Imported from
# the package (not re-implemented) so a future change to the predicate cannot
# drift between the commit path and this, the file path.
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
try:
    from zo_sentinel.gates.hollow import hollow_service_member_scan  # noqa: E402
except ImportError:  # pragma: no cover -- gate must exist; fail loud, never silently pass
    raise


def _dockerfile_text():
    try:
        with open(DOCKERFILE, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None

_APIROUTER_MARK = "API" + "Router("
_ROUTER_DECORATOR = "@" + "router."
ROUTE_DECOR = re.compile(re.escape(_ROUTER_DECORATOR) + r"(get|post|put|delete|patch)\(\s*[\"']([^\"']*)[\"']")
PREFIX_DECL = re.compile(re.escape(_APIROUTER_MARK) + r"[^)]*prefix\s*=\s*[\"']([^\"']+)[\"']", re.S)


def _read(p):
    try:
        return open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def _routes_from_file(path):
    src = _read(path)
    if not src:
        return []
    pm = PREFIX_DECL.search(src)
    prefix = pm.group(1) if pm else ""
    return [prefix + m.group(2) for m in ROUTE_DECOR.finditer(src)]


def _load_toml(path):
    if tomllib is None or not os.path.isfile(path):
        return {}
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except (OSError, ValueError):
        return {}


def _active_taken_routes():
    """Declared routes of every ACTIVE service, static (no app import)."""
    taken = {}
    if not os.path.isdir(ACTIVE):
        return taken
    for name in os.listdir(ACTIVE):
        toml_path = os.path.join(ACTIVE, name, "service.toml")
        meta = _load_toml(toml_path).get("service", {})
        ip = meta.get("import_path")
        if not ip:
            continue
        # resolve import_path -> file (dotted package or root module)
        f = os.path.join(ROOT, ip.replace(".", os.sep) + ".py")
        for r in _routes_from_file(f):
            taken[r] = name
    return taken


def _run_contract(name, timeout=120):
    """Run the staged service's own contract as a module. Return (ok, detail)."""
    mod = "services.staged.%s.contract" % name
    try:
        proc = subprocess.run(
            [sys.executable, "-m", mod],
            cwd=ROOT, capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")},
        )
    except subprocess.TimeoutExpired:
        return False, "contract TIMEOUT (%ss)" % timeout
    except OSError as e:
        return False, "contract could not run: %r" % e
    tail = (proc.stdout or "")[-300:] + (proc.stderr or "")[-300:]
    return proc.returncode == 0, ("exit=%d %s" % (proc.returncode, tail.strip()))


def _casing_drift(sdir):
    """Wrong-cased app.models refs in a staged service (FU-031). Named + autofixable."""
    norm_map = _linter.build_map(_linter.canonical_models())
    drift = {}
    for dp, _d, files in os.walk(sdir):
        if "__pycache__" in dp:
            continue
        for fn in files:
            if fn.endswith(".py"):
                drift.update(_linter.scan_text(_read(os.path.join(dp, fn)), norm_map))
    return drift


def evaluate(name, active_routes):
    """Full gate for one staged service dir. Returns a verdict dict."""
    sdir = os.path.join(STAGED, name)
    reasons = []
    meta = _load_toml(os.path.join(sdir, "service.toml")).get("service", {})
    if not meta.get("name") or not meta.get("import_path"):
        reasons.append("service.toml missing/invalid (need name + import_path)")
    # IMAGE SHIPPABILITY (the FU-102 / prod-v64 class, in the ACTOR not the watcher).
    # This script MOVES the folder without rewriting import_path, and
    # generate_spine.py emits that import_path verbatim for import_module. If no
    # Dockerfile COPY carries the path it names, promotion is a guaranteed prod
    # ModuleNotFoundError -- exactly what left 7 services dead on v64. Checked
    # WITHOUT requiring the post-move file to exist yet; see tools/image_ship_check.py.
    _ip = meta.get("import_path")
    if _ip and not would_be_shipped(_ip, _dockerfile_text()):
        reasons.append(
            "import_path %r is carried by no Dockerfile COPY directive -- it would "
            "ModuleNotFoundError in prod on mount. Fix by pointing import_path at a "
            "shipped module, or by adding the tree to the Dockerfile COPY-list." % _ip
        )
    router_path = os.path.join(sdir, "router.py")
    router_src = _read(router_path)
    if not (( _APIROUTER_MARK[:-1] in router_src) or (_ROUTER_DECORATOR in router_src)):
        reasons.append("router.py exposes no router")
    # route collisions vs active
    cand_routes = _routes_from_file(router_path)
    collisions = {r: active_routes[r] for r in cand_routes if r in active_routes}
    if collisions:
        reasons.append("route collision with active: %s" % collisions)
    # near-dup by name
    if os.path.isdir(os.path.join(ACTIVE, name)):
        reasons.append("a service named '%s' is already active" % name)
    # FU-031 harness repair -- AUTONOMOUS, no human-led review: wrong-cased
    # app.models refs would crash the liveness contract with a raw ImportError, so
    # the gate CORRECTS them in place (deterministic, false-positive-free) and
    # proceeds. The contract below is the real gate; casing is mechanical and never
    # a reason to hold for a human.
    casing_fixed = {}
    drift = _casing_drift(sdir)
    if drift:
        _nm = _linter.build_map(_linter.canonical_models())
        for _dp, _dd, _files in os.walk(sdir):
            if "__pycache__" in _dp:
                continue
            for _fn in _files:
                if _fn.endswith(".py"):
                    _res = _linter.lint_file(os.path.join(_dp, _fn), _nm, fix=True)
                    casing_fixed.update(_res.get("drift", {}))
    # FU-236 SEAM 4 -- THIS CONSUMER'S OWN ENUMERATION.
    # The hollow rule was armed on 2026-08-03 at goose_runner, at the publisher and
    # at tests/ci/no_hollow_scaffold.py. All three fire when a file becomes a
    # COMMIT. This script walks the WORKTREE (os.walk of services/staged), and on
    # 2026-08-04 that gap admitted 2 hollow members into the PROMOTE cohort: 7 of
    # the 12 hollow files on disk were UNTRACKED, had never been a PR, and so no
    # armed seam had ever looked at them. This is not a fourth GATE -- it is the
    # third seam's rule, imported, pointed at the enumeration this file actually
    # reads. It runs BEFORE liveness deliberately: a hollow contract's exit-0 is
    # what manufactured contract_ok=True, so it must never reach the subprocess.
    hollow_hits = []
    for _hdp, _hdd, _hfiles in os.walk(sdir):
        if "__pycache__" in _hdp:
            continue
        for _hfn in _hfiles:
            if not _hfn.endswith(".py"):
                continue
            _habs = os.path.join(_hdp, _hfn)
            _hrel = "services/staged/%s/%s" % (
                name, os.path.relpath(_habs, sdir).replace(os.sep, "/"))
            _hwhy = hollow_service_member_scan(_hrel, _read(_habs))
            if _hwhy:
                hollow_hits.append("%s -- %s" % (_hrel, _hwhy))
    if hollow_hits:
        reasons.append("hollow member(s): " + "; ".join(sorted(hollow_hits)))

    # LIVENESS (subprocess) -- the correctness proof; only if static gates pass
    contract_ok, contract_detail = (None, "skipped (static gate failed)")
    if not reasons:
        contract_ok, contract_detail = _run_contract(name)
        if not contract_ok:
            reasons.append("contract FAILED: %s" % contract_detail)
    return {
        "service": name,
        "verdict": "PROMOTE" if not reasons else "HOLD",
        "routes": cand_routes,
        "contract_ok": contract_ok,
        "contract_detail": contract_detail,
        "casing_autofixed": casing_fixed,
        "reasons": reasons,
    }


def scan():
    if not os.path.isdir(STAGED):
        return []
    return sorted(n for n in os.listdir(STAGED)
                  if os.path.isdir(os.path.join(STAGED, n)) and not n.startswith((".", "_")))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Promote staged services to active, gated on liveness.")
    ap.add_argument("--enforce", action="store_true", help="actually move PROMOTE verdicts (else observe)")
    ap.add_argument("--max-per-run", type=int, default=1, help="cap promotions per run (human-gated cohort)")
    ap.add_argument("--regenerate", action="store_true", help="run generate_spine --emit after a promotion")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    active_routes = _active_taken_routes()
    candidates = scan()
    verdicts = [evaluate(n, active_routes) for n in candidates]

    promoted = []
    if args.enforce:
        for v in verdicts:
            if v["verdict"] != "PROMOTE":
                continue
            if len(promoted) >= args.max_per_run:
                v["verdict"] = "HOLD"
                v["reasons"].append("deferred: per-run cap (%d) reached" % args.max_per_run)
                continue
            os.rename(os.path.join(STAGED, v["service"]), os.path.join(ACTIVE, v["service"]))
            promoted.append(v["service"])
        if promoted and args.regenerate:
            subprocess.run([sys.executable, os.path.join(ROOT, "tools", "generate_spine.py"),
                            "--emit", "."], cwd=ROOT)

    report = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "mode": "enforce" if args.enforce else "observe",
        "candidate_count": len(candidates),
        "promote_count": sum(1 for v in verdicts if v["verdict"] == "PROMOTE"),
        "hold_count": sum(1 for v in verdicts if v["verdict"] == "HOLD"),
        "promoted": promoted,
        "verdicts": verdicts,
    }
    os.makedirs(os.path.dirname(ARTIFACT), exist_ok=True)
    with open(ARTIFACT, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)

    if not args.quiet:
        print("=== staged->active promotion (%s) ===" % report["mode"])
        print("  candidates: %d  promote-eligible: %d  hold: %d  moved: %d"
              % (report["candidate_count"], report["promote_count"],
                 report["hold_count"], len(promoted)))
        for v in verdicts:
            print("  [%s] %s%s" % (v["verdict"], v["service"],
                                   "" if not v["reasons"] else "  -- " + "; ".join(v["reasons"])))
        if not args.enforce and report["promote_count"]:
            print("  observe mode: nothing moved. Re-run with --enforce --max-per-run N to promote.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
