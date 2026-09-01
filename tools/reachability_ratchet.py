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
    observe           -- always exit 0; record the census.
    enforce (2026-07-21 CofC ruling) -- exit 1 on regression. A PR that adds an
                         unmounted router passes by EITHER mounting it OR naming
                         it in tools/reachability_deferred.json with a one-line
                         reason. You may not add one silently.

    python tools/reachability_ratchet.py                 # observe
    python tools/reachability_ratchet.py --enforce       # blocking
    python tools/reachability_ratchet.py --update-baseline
    python tools/reachability_ratchet.py --quiet

WHY A DEFERRED LIST RATHER THAN A HARD BLOCK (CofC 2026-07-21)
--------------------------------------------------------------
The builder is structurally incapable of mounting anything: the
`module_from_exemplar` lane guard forbids it and edit-class directives carry
`output_file: null`, which no-ops 4 of 6 build gates. A naive --enforce would
therefore be an UNSATISFIABLE predicate -- ~15 builder PRs/day would go red for
a rule none of them could ever comply with, and the failure would present as
model regression rather than policy contradiction (the council's Seat 1
objection, which is correct against a naive enforce and void against this one).
The deferred list makes the predicate satisfiable without mounting: declaring is
not mounting. What stops today is SILENT orphan growth, not orphan growth.

THE DEFERRED LIST IS NOT A LOOPHOLE
-----------------------------------
Three integrity checks keep it from becoming the new graveyard:
  * a deferral for a module that is no longer an orphan (mounted, renamed or
    deleted) is STALE and fails enforce -- stale entries silently inflate the
    headroom, which is exactly how an exemption list goes decorative while
    still reporting green.
  * every deferral needs a one-line reason. Reasonless deferrals fail.
  * every exemption in reachability_exempt.json needs a reason too, and
    exempted_count is reported on every run so it can be alarmed on.
The list is capped by review, not by code: >40 active deferrals is a documented
reopen trigger for the council, and is printed loudly here.

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
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASELINE_PATH = os.path.join(ROOT, "tools", "reachability_baseline.json")
EXEMPT_PATH = os.path.join(ROOT, "tools", "reachability_exempt.json")
DEFERRED_PATH = os.path.join(ROOT, "tools", "reachability_deferred.json")
ARTIFACT_DIR = os.path.join(ROOT, "artifacts")
ARTIFACT_PATH = os.path.join(ARTIFACT_DIR, "reachability_ratchet.json")

# >40 active deferrals = the hatch has become the new graveyard.
# Documented REOPEN TRIGGER in the 2026-07-21 CofC ruling. Do not raise this
# number to make the warning quiet; escalate to the chairman instead.
#
# MERGE_AUDIT_2026-08-23 G4: this cap was ADVISORY. Being over it printed the
# reopen trigger inside a check that exited 0, so it appeared in no PR status and
# blocked nothing -- "an instrument reporting faithfully into a place nobody
# reads", which is the failure mode this file's own comments keep naming. It is
# still printed, because the ruling's escalation is still owed. What BLOCKS now
# is the derivative: see DEFERRED NON-INCREASING below.
DEFERRED_REVIEW_CAP = 40

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


def _entries(raw):
    """Normalise {stem: reason} | {stem: {reason}} | [stem] | [{module,reason}].

    A bare string entry maps to an EMPTY reason on purpose -- that is what the
    reasonless checks below are looking for.
    """
    out = {}
    if isinstance(raw, dict):
        for stem, val in raw.items():
            if isinstance(val, dict):
                out[str(stem)] = str(val.get("reason", "")).strip()
            else:
                out[str(stem)] = str(val).strip()
    elif isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                stem = item.get("module") or item.get("stem")
                if stem:
                    out[str(stem)] = str(item.get("reason", "")).strip()
            else:
                out[str(item)] = ""
    return out


def _load_entries(path, key):
    if not os.path.exists(path):
        return {}
    try:
        return _entries(json.load(open(path, encoding="utf-8")).get(key, {}))
    except (OSError, ValueError):
        return {}


def load_deferred():
    """{module_stem: reason} -- routers a PR declined to mount, with a reason."""
    return _load_entries(DEFERRED_PATH, "deferred")


def load_exempt_entries():
    """{module_stem: reason} -- routers mounted by some other mechanism."""
    return _load_entries(EXEMPT_PATH, "exempt")


def census():
    exempt_entries = load_exempt_entries()
    exempt = set(exempt_entries)
    deferred = load_deferred()

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

    # A deferred module is STILL an orphan -- the census stays truthful. The
    # declaration buys headroom against the ratchet, it does not launder the
    # number. Anything else and the artifact stops describing reality.
    orphan_stems = {o["module"] for o in orphans}

    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "router_modules_total": len(mounted) + len(orphans) + len(exempted),
        "mounted": sorted(mounted),
        "mounted_count": len(mounted),
        "exempted": sorted(exempted),
        "exempted_count": len(exempted),
        "orphan_count": len(orphans),
        "orphans": sorted(orphans, key=lambda d: d["module"]),
        # --- CofC 2026-07-21: the deferred hatch and its integrity checks ---
        "deferred_active": sorted(set(deferred) & orphan_stems),
        "deferred_stale": sorted(set(deferred) - orphan_stems),
        "deferred_reasonless": sorted(s for s, r in deferred.items() if not r),
        "deferred_declared_count": len(deferred),
        "exempt_reasonless": sorted(s for s, r in exempt_entries.items() if not r),
    }


def load_baseline():
    if not os.path.exists(BASELINE_PATH):
        return None
    try:
        return int(json.load(open(BASELINE_PATH, encoding="utf-8"))["orphan_count"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def load_deferred_baseline():
    """Recorded size of the deferred list. None if the baseline predates it."""
    if not os.path.exists(BASELINE_PATH):
        return None
    try:
        v = json.load(open(BASELINE_PATH, encoding="utf-8")).get("deferred_count")
        return None if v is None else int(v)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def deferred_count_at(rev):
    """Size of the deferred list in <rev>'s COMMITTED copy, or None.

    None whenever the answer cannot be known -- a shallow CI checkout has no
    HEAD~1, and the file may not have existed yet. None is never treated as
    zero: an unknown previous size disables this comparison rather than
    silently asserting the list grew from nothing.
    """
    try:
        out = subprocess.run(
            ["git", "show", "%s:tools/reachability_deferred.json" % rev],
            cwd=ROOT, capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        return len(_entries(json.loads(out.stdout).get("deferred", {})))
    except ValueError:
        return None


def write_baseline(count, note, deferred_count=None):
    payload = {
        "orphan_count": count,
        "set_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "note": note,
    }
    if deferred_count is not None:
        payload["deferred_count"] = deferred_count
    with open(BASELINE_PATH, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
        fh.write("\n")


def main():
    quiet = "--quiet" in sys.argv
    enforce = "--enforce" in sys.argv
    update = "--update-baseline" in sys.argv

    data = census()
    count = data["orphan_count"]
    baseline = load_baseline()

    active_deferred = data["deferred_active"]
    # Declaring is not mounting: a declared orphan still counts in orphan_count
    # but it buys the PR headroom against the ratchet.
    effective = count - len(active_deferred)

    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    data["baseline"] = baseline
    data["delta"] = None if baseline is None else count - baseline
    data["mode"] = "enforce" if enforce else "observe"
    data["deferred_active_count"] = len(active_deferred)
    data["effective_orphan_count"] = effective
    data["effective_delta"] = None if baseline is None else effective - baseline
    with open(ARTIFACT_PATH, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)

    if update:
        write_baseline(count, "ratchet updated by --update-baseline",
                       deferred_count=len(active_deferred))
        print("baseline updated -> orphans=%d deferred=%d"
              % (count, len(active_deferred)))
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
    if active_deferred:
        print("  deferred (declared, unmounted): %d  -> effective=%d delta=%+d"
              % (len(active_deferred), effective, effective - baseline))

    failures = []

    # --- integrity checks: this is how a hatch stops being decorative ---
    if data["deferred_stale"]:
        print("\n  STALE DEFERRALS (%d) -- declared, but no longer orphans. These"
              % len(data["deferred_stale"]))
        print("  silently inflate the headroom; remove them from "
              "tools/reachability_deferred.json:")
        for s in data["deferred_stale"][:20]:
            print("    %s" % s)
        failures.append("stale deferrals")

    if data["deferred_reasonless"]:
        print("\n  REASONLESS DEFERRALS (%d) -- a deferral needs a one-line reason:"
              % len(data["deferred_reasonless"]))
        for s in data["deferred_reasonless"][:20]:
            print("    %s" % s)
        failures.append("reasonless deferrals")

    if data["exempt_reasonless"]:
        print("\n  REASONLESS EXEMPTIONS (%d) in tools/reachability_exempt.json."
              % len(data["exempt_reasonless"]))
        print("  An exemption without a reason is how this gate goes decorative "
              "while still reporting green:")
        for s in data["exempt_reasonless"][:20]:
            print("    %s" % s)
        failures.append("reasonless exemptions")

    if data["exempted_count"]:
        print("\n  NOTE: exempted_count=%d (was 0 when the ratchet was armed on "
              "2026-07-21). Exemptions are alarmed on by the daily trend check."
              % data["exempted_count"])

    if len(active_deferred) > DEFERRED_REVIEW_CAP:
        print("\n  DEFERRED LIST OVER CAP: %d > %d. Per the 2026-07-21 CofC ruling "
              "this is a REOPEN TRIGGER -- the hatch has become the new graveyard. "
              "Escalate to the chairman; do not raise the cap to make this quiet."
              % (len(active_deferred), DEFERRED_REVIEW_CAP))

    # --- DEFERRED NON-INCREASING (MERGE_AUDIT_2026-08-23 G4) ----------------
    # The absolute cap of 40 is an arbitrary threshold and, being advisory, it
    # blocked nothing while the list grew to 63 and reachability_deferred.json
    # became the single highest-churn file of the whole merge window at 64
    # touches -- roughly one write per day, each one declaring an orphan rather
    # than mounting it.
    #
    # The rule that actually stops accumulation is on the DERIVATIVE, exactly as
    # the orphan ratchet already is: the deferred list MAY NOT GROW. That blocks
    # the next write without requiring the existing 63 to be triaged first, so it
    # does not hold up unrelated work -- the same reasoning that pinned
    # orphan_count at its current level instead of an aspirational one.
    #
    # Two independent references, and the STRICTER wins: the size recorded in
    # reachability_baseline.json, and the size in the previous commit. CI checks
    # out shallow, so HEAD~1 is usually unavailable there and the recorded
    # baseline carries it; locally both apply. An unknown reference is skipped,
    # never read as zero.
    refs = []
    recorded = load_deferred_baseline()
    if recorded is not None:
        refs.append((recorded, "baseline file"))
    prev_commit = deferred_count_at("HEAD~1")
    if prev_commit is not None:
        refs.append((prev_commit, "previous commit"))

    if not refs:
        print("\n  NOTE: no deferred-count reference (baseline predates the field "
              "and no previous commit is reachable). Record one with "
              "--update-baseline; growth is UNGATED until you do.")
    else:
        limit_n, src = min(refs)
        now_n = len(active_deferred)
        if now_n > limit_n:
            print("\n  DEFERRED LIST GREW: %d > %d (%s). The deferral hatch is for "
                  "not blocking a build that structurally cannot mount -- it is not "
                  "storage. Mount the router, or retire it, or take one off the list "
                  "to make room." % (now_n, limit_n, src))
            failures.append("deferred list grew (%d > %d)" % (now_n, limit_n))
        elif now_n < limit_n:
            print("  deferred list shrank (%d -> %d vs %s); re-pin with "
                  "--update-baseline to lock the gain in."
                  % (limit_n, now_n, src))

    if effective > baseline:
        excess = effective - baseline
        print("\n  %d new unmounted router(s) neither mounted nor declared." % excess)
        print("  Fix EITHER way:")
        print("    * mount it under app/, or")
        print("    * add it to tools/reachability_deferred.json with a one-line reason.")
        print("  A module that mounts nowhere is inventory, not a build.")
        failures.append("undeclared new orphans")
    elif delta < 0:
        print("  ratchet can tighten: commit the new baseline with "
              "`python tools/reachability_ratchet.py --update-baseline` (%d -> %d)."
              % (baseline, count))

    if failures:
        if enforce:
            print("\nFAIL (enforce): %s" % ", ".join(failures))
            return 1
        print("\n  observe mode -- not failing on: %s" % ", ".join(failures))
    return 0


if __name__ == "__main__":
    sys.exit(main())
