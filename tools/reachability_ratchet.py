#!/usr/bin/env python3
"""reachability_ratchet.py -- the orphan ratchet.

WHY THIS EXISTS (2026-07-19 reachability postmortem)
----------------------------------------------------
559 modules were shipped by the ladder; 16 became load-bearing. Every gate we
had was GREEN throughout, because every assertion we wrote was either
DIFFERENTIAL (snapshot/parity -- detects change, never absence) or LOCAL (does
this one file satisfy a rule about itself). Neither kind can express the
property that actually mattered, which is global and existential:

    for every router defined in this repo, there exists a mount that makes it
    reachable.

`zo_sentinel/gates/hollow.py` looked like it enforced this -- its docstring says
"not wired to the real system" -- but its REAL regex matches
`from app.db|app.models|get_session|import verdict_breakdown_api`. That is
INBOUND wiring: "is this module connected to the database?". It never asks
"is anything connected to this module?". 324 of 371 orphans (87%) pass it, and
the exemplar doctrine -- which worked -- made every clone resemble the exemplar,
so the gate's discriminating power fell to zero exactly as the doctrine
succeeded.

This module adds the missing assertion CLASS. It does not replace the hollow
gate; it asks the orthogonal question.

WHAT IT DOES
------------
Counts root-level modules that expose an HTTP router but are not mounted by
anything under app/, and compares that count to a committed baseline.

    count > baseline  -> REGRESSION. Non-zero exit when enforcing.
    count == baseline -> hold.
    count < baseline  -> improvement; prints the new number to commit.

The baseline is a RATCHET, not a target: it requires fixing nothing today, it
only stops the number going up. That is the whole point -- a cleanup sprint for
371 modules is not a prerequisite for arresting the bleed.

MODES
-----
    observe (default) -- always exit 0; record the census. Use while the
                         mounts.toml seam does not exist yet, so the builder
                         has no way to comply and blocking would only wedge
                         the publisher queue behind un-mergeable PRs.
    enforce           -- exit 1 on regression. Flip to this once a module CAN
                         mount itself (one-line append to app/mounts.toml).

    python tools/reachability_ratchet.py                 # observe
    python tools/reachability_ratchet.py --enforce       # blocking
    python tools/reachability_ratchet.py --update-baseline
    python tools/reachability_ratchet.py --quiet

Every run writes artifacts/reachability_ratchet.json: the count, the delta, and
the full orphan census with the shape of each module (declared prefix, tags,
route count, whether it imports the data layer). That census is the design input
for app/mounts.toml -- it tells us what a mount declaration actually has to
carry.

Pure stdlib static scan. No network, no DB, no LLM. Safe in CI, on the box, on
the tower.
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_PATH = os.path.join(ROOT, "tools", "reachability_baseline.json")
EXEMPT_PATH = os.path.join(ROOT, "tools", "reachability_exempt.json")
ARTIFACT_DIR = os.path.join(ROOT, "artifacts")
ARTIFACT_PATH = os.path.join(ARTIFACT_DIR, "reachability_ratchet.json")

# A module "exposes a router" if it constructs an APIRouter or decorates one.
ROUTER_DEF = re.compile(r"APIRouter\s*\(|@router\.(get|post|put|delete|patch)")
# Mount surface: anything under app/ that could import a root module.
MOUNT_DIRS = ("app",)
DATA_LAYER = re.compile(r"from app\.(db|models)|import app\.db|app\.models import")


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return ""


def root_modules():
    """Root-level .py files -- the publisher's output location."""
    return sorted(
        f for f in os.listdir(ROOT)
        if f.endswith(".py") and os.path.isfile(os.path.join(ROOT, f))
    )


def mount_surface_text():
    """Concatenated source of everything that could mount a router."""
    blob = []
    for d in MOUNT_DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        for dirpath, _dirs, files in os.walk(base):
            for fn in files:
                if fn.endswith((".py", ".toml", ".json", ".txt")):
                    blob.append(_read(os.path.join(dirpath, fn)))
    return "\n".join(blob)


def describe(path, src):
    """Shape of a router module -- the design input for a mount declaration."""
    routes, tags, prefix = [], set(), None
    for m in re.finditer(r"@router\.(get|post|put|delete|patch)\(\s*[\"']([^\"']+)", src):
        routes.append("%s %s" % (m.group(1).upper(), m.group(2)))
    for m in re.finditer(r"tags\s*=\s*\[([^\]]*)\]", src):
        for t in re.findall(r"[\"']([^\"']+)[\"']", m.group(1)):
            tags.add(t)
    pm = re.search(r"APIRouter\([^)]*prefix\s*=\s*[\"']([^\"']+)", src, re.S)
    if pm:
        prefix = pm.group(1)
    try:
        ast.parse(src)
        parses = True
    except SyntaxError:
        parses = False
    return {
        "module": path[:-3],
        "routes": routes,
        "route_count": len(routes),
        "declared_prefix": prefix,
        "tags": sorted(tags),
        "imports_data_layer": bool(DATA_LAYER.search(src)),
        "parses": parses,
        "lines": src.count("\n") + 1,
    }


def census():
    exempt = set()
    if os.path.exists(EXEMPT_PATH):
        try:
            exempt = set(json.load(open(EXEMPT_PATH, encoding="utf-8")).get("exempt", []))
        except (OSError, ValueError):
            exempt = set()

    surface = mount_surface_text()
    mounted, orphans, exempted = [], [], []

    for fn in root_modules():
        src = _read(os.path.join(ROOT, fn))
        if not ROUTER_DEF.search(src):
            continue
        stem = fn[:-3]
        if stem in exempt:
            exempted.append(stem)
        elif re.search(r"\b%s\b" % re.escape(stem), surface):
            mounted.append(stem)
        else:
            orphans.append(describe(fn, src))

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "router_modules_total": len(mounted) + len(orphans) + len(exempted),
        "mounted": sorted(mounted),
        "mounted_count": len(mounted),
        "exempted": sorted(exempted),
        "exempted_count": len(exempted),
        "orphan_count": len(orphans),
        "orphans": sorted(orphans, key=lambda d: d["module"]),
    }


def load_baseline():
    if not os.path.exists(BASELINE_PATH):
        return None
    try:
        return int(json.load(open(BASELINE_PATH, encoding="utf-8"))["orphan_count"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_baseline(count, note):
    with open(BASELINE_PATH, "w", encoding="utf-8") as fh:
        json.dump({
            "orphan_count": count,
            "set_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": note,
        }, fh, indent=2)
        fh.write("\n")


def main():
    quiet = "--quiet" in sys.argv
    enforce = "--enforce" in sys.argv
    update = "--update-baseline" in sys.argv

    data = census()
    count = data["orphan_count"]
    baseline = load_baseline()

    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    data["baseline"] = baseline
    data["delta"] = None if baseline is None else count - baseline
    data["mode"] = "enforce" if enforce else "observe"
    with open(ARTIFACT_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)

    if update:
        write_baseline(count, "ratchet updated by --update-baseline")
        print("baseline updated -> %d" % count)
        return 0

    if not quiet:
        print("\n=== reachability ratchet @ %s ===" % ROOT)
        print("  router modules: %d  | mounted: %d  | exempt: %d  | ORPHANED: %d"
              % (data["router_modules_total"], data["mounted_count"],
                 data["exempted_count"], count))
        if count:
            print("\n  UNMOUNTED ROUTERS (%d) -- define a router, nothing under app/ imports them:" % count)
            for o in data["orphans"][:40]:
                rt = ("%d route(s)" % o["route_count"]) if o["route_count"] else "no literal routes"
                print("    %-48s %s" % (o["module"], rt))
            if count > 40:
                print("    ... and %d more (full census in artifacts/reachability_ratchet.json)"
                      % (count - 40))

    if baseline is None:
        print("\nverdict: NO-BASELINE  (run --update-baseline to set the ratchet)")
        return 0

    delta = count - baseline
    verdict = "REGRESSION" if delta > 0 else ("IMPROVED" if delta < 0 else "HOLD")
    print("\nverdict: %s  (orphans=%d baseline=%d delta=%+d mode=%s)"
          % (verdict, count, baseline, delta, data["mode"]))

    if delta > 0:
        print("  %d new unmounted router(s). A module that mounts nowhere is inventory, not a build." % delta)
        if enforce:
            return 1
        print("  observe mode -- not failing. Flip to --enforce once app/mounts.toml exists.")
    elif delta < 0:
        print("  ratchet can tighten: commit the new baseline with "
              "`python tools/reachability_ratchet.py --update-baseline` (%d -> %d)." % (baseline, count))
    return 0


if __name__ == "__main__":
    sys.exit(main())
