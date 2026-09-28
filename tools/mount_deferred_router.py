#!/usr/bin/env python3
"""mount_deferred_router.py -- mount a deferred root-level router, or refuse and say why.

WHY THIS EXISTS (measured 2026-09-28, origin/main bf0a5a25f)
------------------------------------------------------------
`tools/reachability_deferred.json` entries all end "Remove this entry when it is
mounted or deleted", and `tools/orphanage.py` labels the promotion candidates
MOUNTABLE -- "clean + data-wired: promotion candidate (keep, mount)".

That label is a STATIC TEXT SCAN. It never imports the module. Measured on the
62 live deferrals:

    deferred labelled MOUNTABLE ................ 28
      import succeeds and exposes `router` ..... 12
      ImportError / ModuleNotFoundError ........ 16   <-- would have been mounted
    of the 12, route path already served by a
      mounted spine entry ...................... 1    <-- SUPERSEDED, missed

So 17 of 28 "promotion candidates" were not mountable, and the triage arithmetic
on #3996/#4004/#4005 ("mount the 34 and the deferred list reaches the cap") was
computed on that label. HARNESS_DOCTRINE R1: the artifact you inspected (source
text) is not the artifact that runs (the imported module). R6: a classifier that
never asked about import health does not get to report 0 import failures.

This tool therefore mounts nothing it has not OBSERVED importing, and nothing
whose routes collide with a route already served. A refusal names the module and
the pole that failed.

WHAT A MOUNT IS
---------------
`tools/generate_spine.py`: "source of truth : services/active/*/service.toml
(presence == registration)". So one mount == one `service.toml` + removing the
deferral. `app/_spine_generated.py` calls `app.include_router(router)` with no
prefix argument, so `prefix` in the toml is DESCRIPTIVE metadata (used by the
manifest and duplicate-route report), not applied at mount time.

IDEMPOTENT BY CHARACTER
-----------------------
Re-running is a no-op. A half-applied mount is HEALED, never duplicated and
never fatal:

    toml present + deferral gone  -> ALREADY_MOUNTED
    toml present + deferral there -> HEALED_DEFERRAL   (drops the stale deferral)
    toml absent  + deferral gone  -> HEALED_TOML       (writes the missing toml)

USAGE
-----
    python tools/mount_deferred_router.py --list
    python tools/mount_deferred_router.py --module axis_scores_query_api
    python tools/mount_deferred_router.py --module axis_scores_query_api --apply
    python tools/mount_deferred_router.py --all-mountable --apply
    python tools/mount_deferred_router.py --module X --json

Without --apply nothing is written (dry run). Exit codes:

    0  every requested module ended MOUNTED / ALREADY_MOUNTED / HEALED_*
    1  at least one module was REFUSED  (this is the negative control)
    2  the tool could not establish its own basis (census or spine unreadable)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFERRED_PATH = os.path.join(ROOT, "tools", "reachability_deferred.json")
ACTIVE_DIR = os.path.join(ROOT, "services", "active")

# One child process per module on purpose: a module that raises at import time,
# calls sys.exit, or corrupts interpreter state cannot take the probe with it.
_PROBE_SRC = r'''
import json, sys
name = sys.argv[1]
try:
    mod = __import__(name)
    for part in name.split(".")[1:]:
        mod = getattr(mod, part)
except BaseException as exc:                      # noqa: BLE001 -- reported, not swallowed
    print(json.dumps({"import_ok": False,
                      "error": type(exc).__name__ + ": " + str(exc)[:200]}))
    raise SystemExit(0)
router = getattr(mod, "router", None)
if router is None:
    print(json.dumps({"import_ok": True, "has_router": False, "paths": []}))
    raise SystemExit(0)
try:
    paths = sorted({getattr(r, "path", "") for r in getattr(router, "routes", [])})
except BaseException as exc:                      # noqa: BLE001
    print(json.dumps({"import_ok": True, "has_router": True, "paths": [],
                      "error": "route read failed: " + repr(exc)[:160]}))
    raise SystemExit(0)
print(json.dumps({"import_ok": True, "has_router": True, "paths": paths}))
'''

TOML_TEMPLATE = """[service]
name = "{name}"
import_path = "{name}"
prefix = "{prefix}"
tag = ""
origin = "live"
auth = "public"
needs_data_layer = {needs_data}

# Mounted by tools/mount_deferred_router.py after OBSERVING the import succeed and
# the routes not collide. Live pre-SOA router: the code stays at its module path;
# this file registers it. See that tool's docstring for why a static MOUNTABLE
# label was not accepted as evidence.
"""


# --------------------------------------------------------------------------- io

def _probe(module):
    """Import `module` in a child and report import health + route paths.

    The probe source is handed to the child as `-c` argv, never written to a file:
    a temp `.py` in the repo ROOT would be counted by `reachability_ratchet
    .root_modules()` and perturb the very census this tool measures. It is argv
    from Python, so no shell ever parses it.
    """
    proc = subprocess.run([sys.executable, "-c", _PROBE_SRC, module],
                          capture_output=True, text=True, cwd=ROOT, timeout=240)
    lines = [l for l in (proc.stdout or "").splitlines() if l.strip()]
    if not lines:
        return {"import_ok": False,
                "error": "probe produced no output (rc=%d) %s"
                         % (proc.returncode, (proc.stderr or "")[-160:])}
    try:
        return json.loads(lines[-1])
    except ValueError:
        return {"import_ok": False,
                "error": "probe output unparseable: " + lines[-1][:160]}


def _load_deferred():
    with open(DEFERRED_PATH, "rb") as fh:
        raw = fh.read()
    newline = "\r\n" if b"\r\n" in raw else "\n"
    return json.loads(raw.decode("utf-8")), newline


def _save_deferred(doc, newline):
    body = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    with open(DEFERRED_PATH, "w", encoding="utf-8", newline=newline) as fh:
        fh.write(body)


def _census():
    """Live orphan census from tools/orphanage.py (read-only)."""
    proc = subprocess.run([sys.executable, os.path.join("tools", "orphanage.py"), "--json"],
                          capture_output=True, text=True, cwd=ROOT, timeout=900)
    if proc.returncode != 0:
        raise RuntimeError("orphanage.py --json rc=%d: %s"
                           % (proc.returncode, (proc.stderr or "")[-300:]))
    data = json.loads(proc.stdout)
    return {o["module"]: o for o in data["orphans"]}


def _spine_entries():
    import importlib.util
    path = os.path.join(ROOT, "app", "_spine_generated.py")
    spec = importlib.util.spec_from_file_location("_spine_probe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return list(mod.SPINE_MOUNTS)


def _mounted_route_paths(verbose=True):
    """Route paths ALREADY served, resolved by importing each spine entry.

    R1: resolved from the objects that mount, not from a regex over source.
    """
    served = {}
    for entry in _spine_entries():
        info = _probe(entry["import_path"])
        for p in info.get("paths", []):
            served.setdefault(p, entry["name"])
    if verbose:
        print("  basis: %d route path(s) already served by %d spine entry(ies)"
              % (len(served), len(_spine_entries())))
    return served


# ---------------------------------------------------------------------- verdict

def _toml_path(name):
    return os.path.join(ACTIVE_DIR, name, "service.toml")


def _decide(name, deferred, census, served):
    """Return (verdict, detail, probe) for one module. Writes nothing."""
    src = os.path.join(ROOT, name + ".py")
    has_toml = os.path.isfile(_toml_path(name))
    is_deferred = name in deferred

    if not os.path.isfile(src):
        return "REFUSED_NO_FILE", "no %s.py at the repo root" % name, None
    if has_toml and not is_deferred:
        return "ALREADY_MOUNTED", "services/active/%s/service.toml present" % name, None
    if has_toml and is_deferred:
        return "HEALED_DEFERRAL", "toml already present; dropping the stale deferral", None
    if not is_deferred and not has_toml:
        return ("REFUSED_NOT_DEFERRED",
                "not in reachability_deferred.json -- this tool only mounts declared "
                "deferrals, so it cannot be used to mount an undeclared router", None)

    probe = _probe(name)
    if not probe.get("import_ok"):
        return "REFUSED_IMPORT", probe.get("error", "import failed"), probe
    if not probe.get("has_router"):
        return "REFUSED_NO_ROUTER", "imports, but exposes no `router`", probe
    clash = {p: served[p] for p in probe.get("paths", []) if p in served}
    if clash:
        return ("REFUSED_ROUTE_COLLISION",
                "route(s) already served: "
                + ", ".join("%s (by %s)" % (p, w) for p, w in sorted(clash.items())),
                probe)
    return "MOUNT", "%d route(s): %s" % (len(probe["paths"]),
                                         ", ".join(probe["paths"])[:200]), probe


def _apply(name, verdict, probe, deferred, census):
    """Perform the writes for a non-refusal verdict. Idempotent."""
    wrote = []
    if verdict in ("MOUNT", "HEALED_TOML") or not os.path.isfile(_toml_path(name)):
        entry = census.get(name, {})
        prefix = entry.get("declared_prefix") or ""
        needs = "true" if entry.get("imports_data_layer") else "false"
        os.makedirs(os.path.dirname(_toml_path(name)), exist_ok=True)
        body = TOML_TEMPLATE.format(name=name, prefix=prefix, needs_data=needs)
        existing = None
        if os.path.isfile(_toml_path(name)):
            with open(_toml_path(name), encoding="utf-8") as fh:
                existing = fh.read()
        if existing != body:
            with open(_toml_path(name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(body)
            wrote.append("services/active/%s/service.toml" % name)
    if name in deferred:
        del deferred[name]
        wrote.append("tools/reachability_deferred.json (-%s)" % name)
    return wrote


# ------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--module", action="append", default=[],
                    help="deferred module to mount (repeatable)")
    ap.add_argument("--all-mountable", action="store_true",
                    help="every deferral the census labels MOUNTABLE (the label is "
                         "the CANDIDATE set, never the decision -- each one is still "
                         "import-probed and can be refused)")
    ap.add_argument("--list", action="store_true",
                    help="probe every deferral labelled MOUNTABLE and print the verdicts")
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (default is a dry run)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        deferred_doc, newline = _load_deferred()
        deferred = deferred_doc["deferred"]
        census = _census()
    except Exception as exc:                       # noqa: BLE001
        print("BASIS UNAVAILABLE (rc=2, never a pass): %s" % exc, file=sys.stderr)
        return 2

    try:
        candidates = sorted(m for m in deferred
                            if census.get(m, {}).get("why_unmounted") == "MOUNTABLE")
        if args.list or args.all_mountable:
            targets = candidates
        else:
            targets = args.module
        if not targets:
            print("nothing to do: pass --module NAME, --all-mountable or --list")
            return 0

        print("basis: deferred=%d  census orphans=%d  MOUNTABLE deferrals=%d  "
              "mode=%s" % (len(deferred), len(census), len(candidates),
                           "APPLY" if args.apply and not args.list else "dry-run"))
        served = _mounted_route_paths()

        results, changed = [], []
        for name in targets:
            verdict, detail, probe = _decide(name, deferred, census, served)
            if args.apply and not args.list and not verdict.startswith("REFUSED"):
                changed += _apply(name, verdict, probe, deferred, census)
                if verdict == "MOUNT":
                    verdict = "MOUNTED"
                    # a module mounted in THIS run now serves its routes
                    for p in (probe or {}).get("paths", []):
                        served.setdefault(p, name)
            results.append({"module": name, "verdict": verdict, "detail": detail})
            print("  %-24s %s" % (verdict, name))
            print("        %s" % detail[:220])

        if args.apply and not args.list and changed:
            _save_deferred(deferred_doc, newline)
            print("\nwrote %d path(s); deferred list is now %d (was %d)"
                  % (len(changed), len(deferred), len(deferred)
                     + sum(1 for r in results
                           if r["verdict"] in ("MOUNTED", "HEALED_DEFERRAL"))))
            print("NEXT (not run for you -- the generator is the spine's own oracle):")
            print("  python tools/generate_spine.py --emit .")
            print("  python tools/reachability_ratchet.py --enforce")

        refused = [r for r in results if r["verdict"].startswith("REFUSED")]
        if args.json:
            print(json.dumps({"results": results, "refused": len(refused),
                              "deferred_now": len(deferred)}, indent=2))
        print("\n%d requested / %d refused / %d actionable"
              % (len(results), len(refused), len(results) - len(refused)))
        return 1 if refused else 0
    finally:
        pass


if __name__ == "__main__":
    sys.exit(main())
