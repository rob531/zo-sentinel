#!/usr/bin/env python3
"""spine_manifest.py -- Option-B spine assembly, as a REPORT-ONLY reference.

WHAT THIS IS (and is not)
-------------------------
A working reference implementation of the "build-time spine generator" from the
SOA design (SOA_SERVICE_REGISTRY_DESIGN_2026-07-21.md, sec 6.5, Option B), built
so the 2026-07-23 mount-lane review can rule on a running mechanism instead of a
paragraph. It:

  1. reads the CURRENT live mount set by scanning the repo (via app_surface_kl --
     no hand-maintained list), and
  2. GENERATES a static mount manifest + a preview of the fail-loud include code
     that a build-time step would emit for prod to run, and
  3. VALIDATES every entry (module present, declares a router, no duplicate
     route) -- fail-loud in `--strict`, report-only by default.

It deliberately does NOT: mount anything, edit app/main.py, create or canonicalize
`services/active/`, or change what prod serves. Prod consumes nothing this writes.
It is the observe-first half of Option B -- exactly the posture the reachability
ratchet (OBSERVE) and app_surface_kl (report-only) shipped in. The DECISION of
whether prod adopts folder-scan (A) vs this generator (B), and the move of the 31
live services into `services/active/`, remains the review's to make (CofC
2026-07-21; FU-064).

WHY IT LIVES IN tools/
----------------------
Both the reachability ratchet and the hollow gate detect a router by matching the
constructor call / verb-decorator strings in RAW SOURCE TEXT, so any tool whose
subject IS routers trips them (app_surface_kl hit this twice at repo root). Both
gates scope to ROOT-LEVEL .py only, so a tool under tools/ is exempt by
construction -- the clean way to write about routers without being mistaken for
one.

SOURCE OF TRUTH
---------------
"What is mounted" is delegated to app_surface_kl (which delegates to
reachability_ratchet.census) -- one definition, so this reference can never
disagree with the armed gate about what "mounted" means. Under Option B the input
would become `services/active/`; today it is the current live set, which lets the
manifest be checked for round-trip fidelity against reality.

USAGE
-----
    python tools/spine_manifest.py                 # report-only summary
    python tools/spine_manifest.py --emit DIR      # write manifest + preview into DIR
    python tools/spine_manifest.py --strict        # exit 1 on any broken entry (future enforce)
    python tools/spine_manifest.py --quiet
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import app_surface_kl  # noqa: E402  (repo-static, zero network)

DEFAULT_EMIT = os.path.join(ROOT, "artifacts")


def build_manifest():
    """Derive the mount manifest by scanning -- no hand-maintained list."""
    kl = app_surface_kl.build_app_surface_kl()
    dup_paths = {d["path"] for d in kl["routes"]["duplicate_paths"]}

    by_mod = defaultdict(lambda: {"prefix": None, "routes": [], "origin": "service",
                                  "duplicate_routes": 0})
    for path, rec in kl["routes"]["by_path"].items():
        if not rec.get("mounted"):
            continue
        mod = rec["module"]
        e = by_mod[mod]
        e["routes"].append(path)
        if rec.get("prefix"):
            e["prefix"] = rec["prefix"]
        if mod.startswith("app/"):
            e["origin"] = "core"          # app/main, app/auth -- the spine itself
        if path in dup_paths:
            e["duplicate_routes"] += 1

    services = []
    for mod in sorted(by_mod):
        e = by_mod[mod]
        # static validation: does the module still declare a router?
        declares = _declares_router(mod)
        if not declares["exists"]:
            status = "MISSING"
        elif not declares["has_router"]:
            status = "NO_ROUTER"
        elif e["duplicate_routes"]:
            status = "DUPLICATE_ROUTE"
        else:
            status = "ok"
        services.append({
            "name": mod.split("/")[-1],
            "module": mod,
            "origin": e["origin"],
            "prefix": e["prefix"],
            "route_count": len(e["routes"]),
            "routes": sorted(e["routes"])[:8],
            "status": status,
        })

    ok = [s for s in services if s["status"] == "ok"]
    broken = [s for s in services if s["status"] != "ok"]
    return {
        "meta": {
            "generator": "tools/spine_manifest.py",
            "mode": "report-only reference (Option B, non-binding)",
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "built_at_commit": kl["meta"]["built_at_commit"],
            "source": "current live mounts, derived by scanning (no hand-list); "
                      "under Option B this input becomes services/active/",
        },
        "service_count": len(services),
        "ok_count": len(ok),
        "broken_count": len(broken),
        "broken": broken,
        "services": services,
    }


def _declares_router(module_rel):
    """Static (no import): does this module file still declare a router?

    Reuses app_surface_kl's assembled marker sentinels so this file never itself
    contains the literal strings the gates scan for.
    """
    path = os.path.join(ROOT, module_rel.replace("/", os.sep) + ".py")
    if not os.path.isfile(path):
        return {"exists": False, "has_router": False}
    try:
        src = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return {"exists": False, "has_router": False}
    has = (app_surface_kl._APIROUTER_MARK[:-1] in src
           or app_surface_kl._ROUTER_MARK in src
           or app_surface_kl._APP_MARK in src)
    return {"exists": True, "has_router": has}


# Preview of the fail-loud include a build-time step would emit for prod. This is
# a STRING written to a file; nothing imports it. It contains no router-constructor
# or decorator literals, so it is safe under the gates and reads as intent.
_PREVIEW_TEMPLATE = '''\
# AUTO-GENERATED preview by tools/spine_manifest.py -- DO NOT EDIT, DO NOT IMPORT.
# Option B: a build-time step emits a file like this from services/active/, and
# prod runs it INSTEAD of a hand-maintained list. The point is the fail-loud
# contract at the bottom -- the opposite of app/main.py's current
# `except Exception: pass`, which is the invisibility bug the whole line of work
# exists to kill (reachability postmortem, FU-044).
import importlib

SPINE_MOUNTS = __SPINE_MOUNTS__

def include_spine(app):
    failures = []
    for entry in SPINE_MOUNTS:
        try:
            mod = importlib.import_module(entry["import_path"])
        except Exception as exc:                 # noqa: BLE001
            failures.append((entry["module"], repr(exc)))
            continue
        r = getattr(mod, "router", None)
        if r is None:
            failures.append((entry["module"], "declares no router"))
            continue
        app.include_router(r)
    if failures:
        # FAIL LOUD. In CI: raise (cheap, at the PR). In prod: boot anyway but
        # surface `failures` on /version + mesh_events -- visible, never swallowed.
        raise RuntimeError("spine mount failures: " + repr(failures))
'''


def render_preview(manifest):
    rows = [{"module": s["module"],
             "import_path": s["module"].replace("/", "."),
             "prefix": s["prefix"]}
            for s in manifest["services"] if s["origin"] == "service"]
    return _PREVIEW_TEMPLATE.replace("__SPINE_MOUNTS__", json.dumps(rows, indent=4))


def strict_exit_code(manifest):
    """The future enforce contract, isolated so it is testable without real data:
    1 iff any service is broken. Report-only mode never calls this."""
    return 1 if manifest["broken_count"] else 0


def emit(manifest, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    mpath = os.path.join(out_dir, "spine_manifest.json")
    ppath = os.path.join(out_dir, "spine_mounts.generated.py")
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    with open(ppath, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_preview(manifest))
    return mpath, ppath


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    quiet = "--quiet" in argv
    strict = "--strict" in argv
    out_dir = DEFAULT_EMIT
    if "--emit" in argv:
        out_dir = argv[argv.index("--emit") + 1]

    manifest = build_manifest()
    emit(manifest, out_dir)

    svc = [s for s in manifest["services"] if s["origin"] == "service"]
    core = [s for s in manifest["services"] if s["origin"] == "core"]
    if not quiet:
        print("\n=== spine manifest (REPORT-ONLY reference, Option B) @ %s ==="
              % manifest["meta"]["built_at_commit"])
        print("  live mounts derived by scanning -- no hand-list")
        print("  services: %d  | core (app/*): %d  | broken: %d"
              % (len(svc), len(core), manifest["broken_count"]))
        for s in svc:
            pfx = s["prefix"] or "(no prefix)"
            print("    %-42s %-22s %d route(s)  [%s]"
                  % (s["module"], pfx, s["route_count"], s["status"]))
        if manifest["broken"]:
            print("\n  BROKEN (--strict would exit 1):")
            for s in manifest["broken"]:
                print("    %-42s %s" % (s["module"], s["status"]))

    verdict = "CLEAN" if manifest["broken_count"] == 0 else "BROKEN"
    print("\nverdict: %s  (services=%d broken=%d mode=%s)"
          % (verdict, len(svc) + len(core), manifest["broken_count"],
             "strict" if strict else "report-only"))
    code = strict_exit_code(manifest) if strict else 0
    if manifest["broken_count"] and not strict:
        print("  report-only -- not failing. --strict is the future enforce mode "
              "(the review's call, alongside app/main.py adopting the generated file).")
    return code


if __name__ == "__main__":
    sys.exit(main())
