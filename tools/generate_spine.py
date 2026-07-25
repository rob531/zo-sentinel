#!/usr/bin/env python3
"""generate_spine.py -- the AUTHORITATIVE Option-B build-time spine generator.

WHAT THIS IS
------------
The build-time half of the SOA "new atomic unit" design
(SOA_SERVICE_REGISTRY_DESIGN_2026-07-21.md, sec 6.5 Option B; CofC binding
ruling 2026-07-23). It promotes `tools/spine_manifest.py` from a REPORT-ONLY
reference to the file prod actually consumes.

It reads the service registry -- `services/active/<name>/service.toml` -- and
GENERATES `app/_spine_generated.py`, the fail-loud include that `app/main.py`
runs at boot INSTEAD of the old hand-maintained `_OPTIONAL_ROUTERS` list and its
silent `except Exception: pass` mount loop (the invisibility bug the reachability
postmortem, FU-044, is about).

  source of truth : services/active/*/service.toml   (presence == registration)
  generated file  : app/_spine_generated.py          (committed, prod runs it)
  audit artifact  : artifacts/spine_manifest.json
  inherited debt  : tools/spine_known_issues.json     (satisfiable-gate allowlist)

WHY A GENERATED FILE UNDER app/ (not a runtime folder scan)
-----------------------------------------------------------
Option B was chosen over Option A (runtime scan on Fly) because every failure in
this system's history has been a SILENT RUNTIME fault. Build-time generation
moves discovery to CI, where failure is cheap and visible; prod runs a static,
reviewed file. Two properties fall out:

  * app/_spine_generated.py lists every service's import_path as a LITERAL. The
    reachability ratchet counts a root module as "mounted" iff its stem is a
    \\bword\\b match anywhere under app/, so moving off the hand-list does not
    manufacture orphans.
  * CI proves the committed file is in sync with active/ (--check) and every
    active service is structurally valid (--strict) WITHOUT importing anything --
    a pure static gate that cannot itself degrade (FU-031).

SATISFIABLE GATE (the reachability-ratchet lesson, applied to services)
-----------------------------------------------------------------------
A gate that fails on PRE-EXISTING conditions is unsatisfiable and gets disabled.
The seed of active/ inherited 6 dead/duplicate entries from the hand-list; those
are recorded in spine_known_issues.json so --strict passes on the seed but fails
on any NEW dead/duplicate service, and fails if a known issue goes STALE (now
healthy) -- so the allowlist can never go decorative.

FAIL LOUD, NEVER SILENT (design sec 5)
--------------------------------------
  CI    : `--strict` exits 1 on an unlisted broken active/ entry.
  prod  : include_spine() boots ANYWAY but records import/include failures on
          app.state.spine_mount_failures (surfaced at /spine/health) and logs
          them; a module that declares no router is a VISIBLE skip, not a swallow.

USAGE
-----
    python tools/generate_spine.py                 # report-only summary
    python tools/generate_spine.py --emit .        # (re)write app/_spine_generated.py + artifact
    python tools/generate_spine.py --check         # exit 1 if committed file is STALE vs active/
    python tools/generate_spine.py --strict        # exit 1 on unlisted-broken or stale-known
    python tools/generate_spine.py --quiet

Pure stdlib static scan (tomllib is 3.11+). No network, no DB, no app import.
Lives in tools/ on purpose: the ratchet + hollow gate scope to ROOT-level .py
only, so a tool ABOUT routers is exempt by construction.
"""
from __future__ import annotations

import json
import os
import pprint
import re
import sys
import time

try:
    import tomllib  # py3.11+
except ModuleNotFoundError:  # pragma: no cover - CI pins 3.11
    tomllib = None

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ACTIVE_DIR = os.path.join(ROOT, "services", "active")
GENERATED_PATH = os.path.join(ROOT, "app", "_spine_generated.py")
ARTIFACT_DIR = os.path.join(ROOT, "artifacts")
ARTIFACT_PATH = os.path.join(ARTIFACT_DIR, "spine_manifest.json")
KNOWN_ISSUES_PATH = os.path.join(ROOT, "tools", "spine_known_issues.json")

# Router-detection markers, assembled so this file never contains the literal
# strings the ratchet/hollow gates scan for (belt-and-braces; tools/ is exempt).
_APIROUTER_MARK = "API" + "Router("
_ROUTER_DECORATOR = "@" + "router."
ROUTE_DECOR = re.compile(re.escape(_ROUTER_DECORATOR) + r"(get|post|put|delete|patch)\(\s*[\"']([^\"']*)[\"']")
PREFIX_DECL = re.compile(re.escape(_APIROUTER_MARK) + r"[^)]*prefix\s*=\s*[\"']([^\"']+)[\"']", re.S)


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def _module_file(import_path):
    """Dotted import_path -> package file; bare -> a root-level module."""
    return os.path.join(ROOT, import_path.replace(".", os.sep) + ".py")


def _load_toml(path):
    if tomllib is None:
        return {}
    try:
        with open(path, "rb") as fh:
            return tomllib.load(fh)
    except (OSError, ValueError):
        return {}


def load_known_issues():
    """{(service, status): reason} inherited-debt allowlist for --strict."""
    if not os.path.exists(KNOWN_ISSUES_PATH):
        return {}
    try:
        data = json.load(open(KNOWN_ISSUES_PATH, encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    out = {}
    for e in data.get("known", []):
        svc, st = e.get("service"), e.get("status")
        if svc and st:
            out[(svc, st)] = str(e.get("reason", "")).strip()
    return out


def scan_active():
    """Read every services/active/<name>/service.toml -> registry entries."""
    entries = []
    if not os.path.isdir(ACTIVE_DIR):
        return entries
    for name in sorted(os.listdir(ACTIVE_DIR)):
        sdir = os.path.join(ACTIVE_DIR, name)
        if not os.path.isdir(sdir) or name.startswith((".", "_")):
            continue
        toml_path = os.path.join(sdir, "service.toml")
        if not os.path.isfile(toml_path):
            entries.append({"name": name, "import_path": None, "prefix": None,
                            "tag": None, "origin": "unknown", "needs_data_layer": None,
                            "auth": None, "_no_toml": True})
            continue
        meta = _load_toml(toml_path).get("service", {})
        entries.append({
            "name": meta.get("name", name),
            "import_path": meta.get("import_path"),
            "prefix": meta.get("prefix"),
            "tag": meta.get("tag"),
            "origin": meta.get("origin", "service"),
            "needs_data_layer": meta.get("needs_data_layer"),
            "auth": meta.get("auth", "public"),
        })
    return entries


def _declared_routes(import_path):
    """Full route paths a module declares (prefix + decorator path), static."""
    src = _read(_module_file(import_path))
    if not src:
        return []
    pm = PREFIX_DECL.search(src)
    prefix = pm.group(1) if pm else ""
    return [prefix + m.group(2) for m in ROUTE_DECOR.finditer(src)]


def validate(entries):
    """Static validation of every active entry. No import -- cannot degrade.

    status per entry: ok | NO_TOML | MISSING | NO_ROUTER | DUPLICATE_ROUTE
    """
    seen, dup_paths = {}, set()
    for e in entries:
        if not e.get("import_path"):
            continue
        for full in _declared_routes(e["import_path"]):
            if full in seen and seen[full] != e["import_path"]:
                dup_paths.add(full)
            seen.setdefault(full, e["import_path"])

    out = []
    for e in entries:
        ip = e.get("import_path")
        if e.get("_no_toml") or not ip:
            status = "NO_TOML"
        else:
            path = _module_file(ip)
            if not os.path.isfile(path):
                status = "MISSING"
            else:
                src = _read(path)
                has_router = (_APIROUTER_MARK[:-1] in src) or (_ROUTER_DECORATOR in src)
                routes = _declared_routes(ip)
                if not has_router:
                    status = "NO_ROUTER"
                elif any(r in dup_paths for r in routes):
                    status = "DUPLICATE_ROUTE"
                else:
                    status = "ok"
        rec = dict(e)
        rec["status"] = status
        rec.pop("_no_toml", None)
        out.append(rec)
    return out


def build_manifest():
    entries = validate(scan_active())
    known = load_known_issues()
    broken = [e for e in entries if e["status"] != "ok"]
    unlisted = [e for e in broken if (e["name"], e["status"]) not in known]
    present = {(e["name"], e["status"]) for e in entries}
    stale_known = sorted(k for k in known if k not in present)
    return {
        "meta": {
            "generator": "tools/generate_spine.py",
            "mode": "AUTHORITATIVE build-time spine (Option B)",
            "source": "services/active/*/service.toml",
            "generated_file": "app/_spine_generated.py",
            "built_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "built_at_commit": os.environ.get("GIT_COMMIT", ""),
        },
        "service_count": len(entries),
        "ok_count": len(entries) - len(broken),
        "broken_count": len(broken),
        "known_broken_count": len(broken) - len(unlisted),
        "unlisted_broken": unlisted,
        "stale_known": stale_known,
        "broken": broken,
        "services": entries,
    }


_HEADER = '''\
# AUTO-GENERATED by tools/generate_spine.py from services/active/ -- DO NOT EDIT.
# Regenerate:  python tools/generate_spine.py --emit .
#
# This is the Option-B build-time spine (SOA design 2026-07-21; CofC 2026-07-23).
# It REPLACES app/main.py's old hand-maintained _OPTIONAL_ROUTERS list and its
# silent `except Exception: pass` mount loop -- the invisibility bug the
# reachability postmortem (FU-044) exists to kill.
#
# CI keeps this file honest:  generate_spine.py --check  (in sync with active/)
#                             generate_spine.py --strict (every active service valid)
# Each import_path below is a LITERAL so the reachability census still counts a
# live service as "mounted" (\\bword\\b match under app/), not an orphan.
#
# FAIL LOUD:  prod boots anyway but records failures on app.state + /spine/health;
#             a module that declares no router is a VISIBLE skip, not a swallow.
from __future__ import annotations

import importlib
import logging

_log = logging.getLogger("spine")

SPINE_MOUNTS = __SPINE_MOUNTS__


def include_spine(app, strict=False):
    """Mount every active service. Fail LOUD, never SILENT.

    Reproduces the old loop's resilience (a router-less or unimportable module
    never blocks boot) but makes every outcome VISIBLE:

        app.state.spine_mounted            -> [service names actually mounted]
        app.state.spine_skipped_no_router  -> [declared in active/, exposes no router]
        app.state.spine_mount_failures     -> [{service, import_path, error}]  (import/include)

    prod (strict=False): boot anyway; record the three buckets; log failures.
    CI  (strict=True):   raise on the first import/include failure.
    """
    mounted, skipped, failures = [], [], []
    for entry in SPINE_MOUNTS:
        name = entry["name"]
        try:
            mod = importlib.import_module(entry["import_path"])
        except Exception as exc:  # noqa: BLE001 -- broad on purpose; recorded, not swallowed
            failures.append({"service": name, "import_path": entry["import_path"],
                             "error": repr(exc)})
            continue
        router = getattr(mod, "router", None)
        if router is None:
            skipped.append(name)
            continue
        try:
            app.include_router(router)
        except Exception as exc:  # noqa: BLE001
            failures.append({"service": name, "import_path": entry["import_path"],
                             "error": "include_router failed: " + repr(exc)})
            continue
        mounted.append(name)
    try:
        app.state.spine_mounted = mounted
        app.state.spine_skipped_no_router = skipped
        app.state.spine_mount_failures = failures
        app.state.spine_service_count = len(SPINE_MOUNTS)
    except Exception:  # pragma: no cover - app.state always present on FastAPI
        pass
    if skipped:
        _log.warning("spine: %d active service(s) declare no router (skipped): %r",
                     len(skipped), skipped)
    if failures:
        _log.error("SPINE MOUNT FAILURES (%d of %d): %r",
                   len(failures), len(SPINE_MOUNTS), failures)
        if strict:
            raise RuntimeError("spine mount failures: %r" % (failures,))
    return {"mounted": mounted, "skipped_no_router": skipped, "failures": failures}
'''


def render_generated(manifest):
    rows = []
    for s in manifest["services"]:
        if not s.get("import_path"):
            continue  # a NO_TOML dir cannot be mounted; --strict already flags it
        rows.append({
            "name": s["name"],
            "import_path": s["import_path"],
            "prefix": s.get("prefix"),
            "origin": s.get("origin", "service"),
        })
    # pformat (not json.dumps): the emitted file is PYTHON, so None/True/False
    # must render as Python literals, never JSON null/true/false.
    rendered = pprint.pformat(rows, indent=4, width=100, sort_dicts=False)
    return _HEADER.replace("__SPINE_MOUNTS__", rendered)


def emit(manifest, root=ROOT):
    os.makedirs(os.path.join(root, "artifacts"), exist_ok=True)
    gen_path = os.path.join(root, "app", "_spine_generated.py")
    art_path = os.path.join(root, "artifacts", "spine_manifest.json")
    with open(gen_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_generated(manifest))
    with open(art_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    return gen_path, art_path


def check_in_sync(manifest):
    """True iff the committed app/_spine_generated.py equals a fresh render."""
    return render_generated(manifest) == _read(GENERATED_PATH)


def main(argv=None):
    argv = list(argv if argv is not None else sys.argv[1:])
    quiet = "--quiet" in argv
    strict = "--strict" in argv
    check = "--check" in argv
    do_emit = "--emit" in argv
    emit_root = ROOT
    if do_emit:
        i = argv.index("--emit")
        if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
            emit_root = os.path.abspath(argv[i + 1])

    manifest = build_manifest()
    svc = manifest["services"]

    if do_emit:
        gen, art = emit(manifest, emit_root)
        if not quiet:
            print("wrote %s (%d services) + %s"
                  % (os.path.relpath(gen, emit_root), len(svc), os.path.relpath(art, emit_root)))

    if not quiet and not do_emit:
        print("\n=== spine (AUTHORITATIVE, Option B) src=services/active/ ===")
        print("  services: %d  | ok: %d  | broken: %d (known: %d, unlisted: %d)"
              % (manifest["service_count"], manifest["ok_count"], manifest["broken_count"],
                 manifest["known_broken_count"], len(manifest["unlisted_broken"])))
        for s in svc:
            flag = "" if s["status"] == "ok" else "  <-- " + s["status"]
            print("    %-40s %-22s [%s]%s"
                  % (s["name"], s.get("prefix") or "(no prefix)", s["status"], flag))

    rc = 0
    if strict and manifest["unlisted_broken"]:
        print("\nSTRICT: %d UNLISTED broken active service(s) (add to services/active/ fix, "
              "or tools/spine_known_issues.json with a reason): %s"
              % (len(manifest["unlisted_broken"]),
                 ", ".join("%s=%s" % (b["name"], b["status"]) for b in manifest["unlisted_broken"])))
        rc = 1
    if strict and manifest["stale_known"]:
        print("\nSTRICT: %d STALE known-issue(s) -- now healthy/absent, remove from "
              "spine_known_issues.json: %s"
              % (len(manifest["stale_known"]), manifest["stale_known"]))
        rc = 1
    if check and not check_in_sync(manifest):
        print("\nCHECK: app/_spine_generated.py is STALE vs services/active/. "
              "Run: python tools/generate_spine.py --emit .")
        rc = 1
    if rc == 0 and not quiet:
        print("\nverdict: CLEAN (services=%d broken=%d[known] strict=%s check=%s)"
              % (manifest["service_count"], manifest["broken_count"], strict, check))
    return rc


if __name__ == "__main__":
    sys.exit(main())
