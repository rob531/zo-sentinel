#!/usr/bin/env python3
"""S1 -- census of services/staged, measured by the REAL promotion gate.

For every directory under services/staged this records:

  family / version      the name with any `_v<N>` suffix stripped, and N (1 if none)
  has_source            at least one .py that is not __init__.py or contract.py
  has_toml / toml_valid service.toml present; [service] carries name + import_path
  gate                  `promote_staged_to_active.evaluate()` verbatim: verdict,
                        reasons, import/contract detail -- the gate, not a bucket
  first_failure         reasons[0] (the acceptance test of any repair directive)
  failure_class         a mechanical label derived from first_failure, used by S5
                        to route: fix-by-script vs builder directive
  footprint_kb          max RSS of a clean interpreter after importing the service
                        module, and its delta over a fastapi-only baseline. Only
                        measured when the import succeeds; otherwise null (R6: an
                        unmeasured footprint is UNKNOWN, never 0)
  runtime_class         declared (service.toml `runtime_class` / `kind`, if any) and
                        observed: api (router with routes) | worker (run()/main loop
                        or a scheduled-job name, no router) | lib (importable source,
                        neither) | none (no source at all)

Re-measure; never trust a bucket label. The gate's `evaluate()` is called on every
directory -- the census and the promoter cannot disagree, because they are the same
function. NOTE the gate autofixes app.models casing drift in place while it
measures (that is S2's harvest): run the census on a branch and commit the diff.

    python tools/staging_drain/census.py                       # -> artifacts/staging_drain/census.json
    python tools/staging_drain/census.py --out _chains/staging_drain/census.json
    python tools/staging_drain/census.py --only foo --only bar --merge <prev.json>
    python tools/staging_drain/census.py --static-only         # no subprocesses (fast shape count)

Exit 0 when the census was written; 2 when staged/ is missing (UNKNOWN, not zero).
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
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

import promote_staged_to_active as gate  # noqa: E402  the real gate

STAGED = os.path.join(ROOT, "services", "staged")
DEFAULT_OUT = os.path.join(ROOT, "artifacts", "staging_drain", "census.json")

VERSION_RE = re.compile(r"^(?P<family>.+?)_v(?P<ver>\d+)$")
NON_SOURCE = {"__init__.py", "contract.py"}
WORKER_NAME_RE = re.compile(
    r"(ingest|ingestor|linker|rollup|feed|normaliz|refresh|sync|job|worker|"
    r"scheduler|batch|exporter|census|probe|report|digest|cron|poller|watch)",
    re.I)
# A worker is evidenced by SOURCE, never by name: an entrypoint (`def run/main/
# run_once/tick/cycle`) or a poll loop. `if __name__ == "__main__"` alone is NOT
# evidence -- every exemplar-mirrored logic.py carries a self-test block, and on
# 2026-10-04 that alone would have called 157 unfinished api scaffolds "workers"
# (measured: 14 carry a real entrypoint).
WORKER_SRC_RE = re.compile(r"^\s*def\s+(run|main|run_once|tick|cycle)\s*\(|^\s*while\s+True\s*:", re.M)
ROUTE_RE = re.compile(r"@router\.(get|post|put|delete|patch)\(")


def _read(p):
    try:
        with open(p, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def split_family(name):
    m = VERSION_RE.match(name)
    if m:
        return m.group("family"), int(m.group("ver"))
    return name, 1


def classify_failure(first_failure, verdict):
    """Mechanical label for S5 routing. 'none' for PROMOTE."""
    f = first_failure or ""
    if verdict == "PROMOTE" and not f:
        return "none"
    if f.startswith("service.toml missing/invalid"):
        return "missing_or_invalid_toml"
    if "carried by no Dockerfile COPY" in f:
        return "unshipped_import_path"
    if f.startswith("router.py exposes no router"):
        return "missing_router"
    if f.startswith("route collision"):
        return "route_collision"
    if "is already active" in f:
        return "name_already_active"
    if f.startswith("hollow member"):
        return "hollow_member"
    if f.startswith("IMPORT FAILED"):
        if "cannot import name" in f:
            return "import_model_name"
        if "No module named" in f:
            return "import_module_not_found"
        if "NameError" in f:
            return "import_undefined_name"
        if "SyntaxError" in f or "IndentationError" in f:
            return "import_syntax_error"
        return "import_other"
    if f.startswith("contract FAILED"):
        if "TIMEOUT" in f:
            return "contract_timeout"
        if "No module named" in f and "contract" in f:
            return "contract_missing"
        return "contract_failed"
    return "other"


def inspect_dir(name):
    """Static shape of one staged dir -- no imports, no subprocesses."""
    sdir = os.path.join(STAGED, name)
    files = []
    for dp, dd, fs in os.walk(sdir):
        dd[:] = [d for d in dd if d != "__pycache__"]
        for fn in fs:
            if fn.endswith((".pyc", ".pyo")):
                continue
            files.append(os.path.relpath(os.path.join(dp, fn), sdir).replace(os.sep, "/"))
    files.sort()
    py = [f for f in files if f.endswith(".py")]
    source = [f for f in py if os.path.basename(f) not in NON_SOURCE]
    toml_path = os.path.join(sdir, "service.toml")
    has_toml = os.path.isfile(toml_path)
    meta = gate._load_toml(toml_path).get("service", {}) if has_toml else {}
    toml_valid = bool(meta.get("name") and meta.get("import_path"))
    declared = None
    for k in ("runtime_class", "kind", "runtime", "class"):
        if isinstance(meta.get(k), str) and meta.get(k):
            declared = meta[k]
            break
    router_src = _read(os.path.join(sdir, "router.py"))
    has_router = bool(router_src) and (("APIRouter(" in router_src) or ("@router." in router_src))
    n_routes = len(ROUTE_RE.findall(router_src)) if router_src else 0
    src_text = "\n".join(_read(os.path.join(sdir, f)) for f in source)
    worker_evidence = bool(source) and bool(WORKER_SRC_RE.search(src_text))
    if has_router or n_routes:
        observed = "api"
    elif worker_evidence:
        observed = "worker"
    elif source:
        observed = "lib"
    else:
        observed = "none"
    # How far the builder's per-file scaffold got (service_decomposer emits
    # __init__ + service.toml by write_raw, then logic/router/contract by engine).
    if not files:
        stage = "empty"
    elif not source:
        stage = "manifest_only"
    elif has_router and "contract.py" in files:
        stage = "complete"
    elif has_router:
        stage = "router_no_contract"
    else:
        stage = "logic_only"
    family, version = split_family(name)
    return {
        "service": name,
        "family": family,
        "version": version,
        "files": files,
        "py_files": len(py),
        "bytes": sum(os.path.getsize(os.path.join(sdir, f)) for f in files if os.path.isfile(os.path.join(sdir, f))),
        "has_source": bool(source),
        "has_toml": has_toml,
        "toml_valid": toml_valid,
        "toml_keys": sorted(meta.keys()) if meta else [],
        "import_path": meta.get("import_path"),
        "has_router": has_router,
        "route_count": n_routes,
        "has_contract": "contract.py" in files,
        "runtime_class_declared": declared,
        "runtime_class_observed": observed,
        "worker_name_hint": bool(WORKER_NAME_RE.search(name)),
        "scaffold_stage": stage,
        "source_modules": [f[:-3].replace("/", ".") for f in source],
    }


def _tree_digest(sdir):
    import hashlib
    h = hashlib.sha256()
    for dp, dd, fs in os.walk(sdir):
        dd[:] = sorted(d for d in dd if d != "__pycache__")
        for fn in sorted(fs):
            if fn.endswith((".pyc", ".pyo")):
                continue
            p = os.path.join(dp, fn)
            h.update(os.path.relpath(p, sdir).encode())
            try:
                with open(p, "rb") as fh:
                    h.update(fh.read())
            except OSError:
                pass
    return h.hexdigest()


_FOOTPRINT_CODE = r"""
import importlib, resource, sys
mod = sys.argv[1]
if mod != "-":
    importlib.import_module(mod)
print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
"""


def _rss_kb(module, timeout=90):
    """Max RSS (KiB on Linux) of a clean interpreter after importing `module`.
    '-' imports nothing beyond the fastapi baseline. None on any failure."""
    code = "import fastapi\n" + _FOOTPRINT_CODE
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code, module], cwd=ROOT, capture_output=True,
            text=True, timeout=timeout,
            env={**os.environ, "PYTHONPATH": ROOT + os.pathsep + os.environ.get("PYTHONPATH", "")},
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if proc.returncode != 0:
        return None
    try:
        v = int(proc.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    if sys.platform == "darwin":  # ru_maxrss is bytes there
        v //= 1024
    return v


def census_one(name, active_routes, baseline_kb, static_only=False):
    rec = inspect_dir(name)
    if static_only:
        rec["gate"] = None
        rec["first_failure"] = None
        rec["failure_class"] = "unmeasured"
        rec["footprint_kb"] = None
        rec["footprint_delta_kb"] = None
        rec["gate_changed_tree"] = None
        rec["source_import"] = None
        rec["source_import_ok"] = None
        return rec
    t0 = time.time()
    sdir = os.path.join(STAGED, name)
    before = _tree_digest(sdir)
    v = gate.evaluate(name, active_routes)
    # The gate autofixes casing while it measures and REPORTS `casing_autofixed`
    # from the linter's drift dict, which is populated even when the rewrite was a
    # no-op (lint_file sets fixed=False, drift non-empty). Measured 2026-10-04:
    # 165 reported sites, 0 bytes changed. The census records the truth: did the
    # directory's bytes change across the gate call?
    rec["gate_changed_tree"] = _tree_digest(sdir) != before
    rec["gate"] = {
        "verdict": v["verdict"],
        "reasons": v["reasons"],
        "routes": v["routes"],
        "import_ok": v["import_ok"],
        "import_detail": v["import_detail"],
        "contract_ok": v["contract_ok"],
        "contract_detail": v["contract_detail"],
        "casing_autofixed": len(v.get("casing_autofixed") or {}),
        "seconds": round(time.time() - t0, 2),
    }
    rec["first_failure"] = v["reasons"][0] if v["reasons"] else ""
    rec["failure_class"] = classify_failure(rec["first_failure"], v["verdict"])
    if v["import_ok"]:
        kb = _rss_kb(gate._staged_equivalent(v["import_path"], name))
        rec["footprint_kb"] = kb
        rec["footprint_delta_kb"] = (kb - baseline_kb) if (kb is not None and baseline_kb is not None) else None
    else:
        rec["footprint_kb"] = None
        rec["footprint_delta_kb"] = None
    # Non-router services never reach the gate's import step (the static router
    # gate fails first), so the router gate says nothing about whether a worker
    # or lib can even be imported. Measure that separately, module by module.
    rec["source_import"] = None
    if rec["has_source"] and not rec["has_router"]:
        results = {}
        for mod in rec["source_modules"]:
            ok, detail = gate._import_check("services.staged.%s.%s" % (name, mod))
            results[mod] = {"ok": ok, "detail": detail}
        rec["source_import"] = results
        rec["source_import_ok"] = all(r["ok"] for r in results.values()) if results else None
    else:
        rec["source_import_ok"] = None
    return rec


def summarize(records):
    from collections import Counter
    c = lambda key: dict(sorted(Counter(r.get(key) for r in records).items(), key=lambda kv: (-kv[1], str(kv[0]))))
    verdicts = Counter((r.get("gate") or {}).get("verdict", "unmeasured") for r in records)
    families = {}
    for r in records:
        families.setdefault(r["family"], []).append(r["service"])
    return {
        "directories": len(records),
        "families": len(families),
        "multi_version_families": sum(1 for v in families.values() if len(v) > 1),
        "verdicts": dict(verdicts),
        "has_source": sum(1 for r in records if r["has_source"]),
        "no_source": sum(1 for r in records if not r["has_source"]),
        "has_toml": sum(1 for r in records if r["has_toml"]),
        "toml_valid": sum(1 for r in records if r["toml_valid"]),
        "has_router": sum(1 for r in records if r["has_router"]),
        "has_contract": sum(1 for r in records if r["has_contract"]),
        "runtime_class_observed": c("runtime_class_observed"),
        "runtime_class_declared": c("runtime_class_declared"),
        "failure_class": c("failure_class"),
        "scaffold_stage": c("scaffold_stage"),
        "gate_changed_tree": sum(1 for r in records if r.get("gate_changed_tree")),
        "source_import_ok": sum(1 for r in records if r.get("source_import_ok") is True),
        "source_import_failed": sum(1 for r in records if r.get("source_import_ok") is False),
        "footprint_delta_kb_measured": sum(1 for r in records if r.get("footprint_delta_kb") is not None),
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--only", action="append", default=[], metavar="NAME")
    ap.add_argument("--merge", metavar="PREV_JSON",
                    help="start from a previous census and replace only the services measured now")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--static-only", action="store_true", help="shape only; do not run the gate")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    if not os.path.isdir(STAGED):
        print("UNKNOWN: %s is not a directory -- no census written" % STAGED)
        return 2
    names = gate.scan()
    if args.only:
        missing = sorted(set(args.only) - set(names))
        if missing:
            print("ERROR: --only name(s) not in staged/: %s" % ", ".join(missing))
            return 2
        names = [n for n in names if n in set(args.only)]

    active_routes = gate._active_taken_routes()
    baseline_kb = None if args.static_only else _rss_kb("-")
    t0 = time.time()
    records = []
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(census_one, n, active_routes, baseline_kb, args.static_only): n for n in names}
        done = 0
        for fut in cf.as_completed(futs):
            n = futs[fut]
            try:
                records.append(fut.result())
            except Exception as exc:  # one broken dir must not lose the census
                rec = {"service": n, "family": split_family(n)[0], "version": split_family(n)[1],
                       "census_error": "%s: %s" % (type(exc).__name__, exc),
                       "has_source": None, "has_toml": None, "toml_valid": None,
                       "runtime_class_observed": "unmeasured", "runtime_class_declared": None,
                       "gate": None, "first_failure": None, "failure_class": "census_error",
                       "footprint_kb": None, "footprint_delta_kb": None}
                records.append(rec)
            done += 1
            if not args.quiet and done % 100 == 0:
                print("  %d/%d measured (%.0fs)" % (done, len(names), time.time() - t0), flush=True)
    records.sort(key=lambda r: r["service"])

    if args.merge and os.path.isfile(args.merge):
        with open(args.merge, encoding="utf-8") as fh:
            prev = json.load(fh)
        by = {r["service"]: r for r in prev.get("services", [])}
        for r in records:
            by[r["service"]] = r
        # drop services that no longer exist in staged/
        current = set(gate.scan())
        records = sorted((r for r in by.values() if r["service"] in current), key=lambda r: r["service"])

    out = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "basis": {
            "repo": ROOT,
            "staged_dir": STAGED,
            "gate": "tools/promote_staged_to_active.py::evaluate",
            "mode": "static-only" if args.static_only else "full-gate",
            "python": sys.version.split()[0],
            "footprint_baseline_kb": baseline_kb,
            "seconds": round(time.time() - t0, 1),
            "git_head": _git_head(),
        },
        "summary": summarize(records),
        "services": records,
    }
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, sort_keys=False)
    if not args.quiet:
        print("=== staging_drain S1 census (%s) ===" % out["basis"]["mode"])
        print(json.dumps(out["summary"], indent=2))
        print("written: %s" % args.out)
    return 0


def _git_head():
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, timeout=10).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        return None


if __name__ == "__main__":
    sys.exit(main())
