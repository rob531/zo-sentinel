#!/usr/bin/env python3
"""What would the census halt have DONE? -- upstream, downstream, and retrospect.

WHY THIS EXISTS
---------------
Arming an actuator because its detector looks right is how this repo acquired
guardrails that cost money without preventing anything. Before `--halt-mode armed`
is switched on, three questions need answers that are measured rather than asserted:

  UPSTREAM     which lanes can even produce a halt, and on what evidence?
               A lane with no validator cannot raise VALIDITY_COLLAPSE, so arming
               changes nothing for it. Saying "the halt protects the builder" when
               4 of 6 lanes are unvalidated would be false comfort.

  PRESENT      how many halts would fire against the queue as it stands right now?
               If the answer is zero, arming today is a no-op -- which is exactly
               what you want to know BEFORE arming, not after.

  RETROSPECT   would it have fired on the incident it was built for?
               A detector that would have missed its own founding case is not
               ready. This replays the 2026-07-29 state (36 open manifest PRs, 0
               valid) through the REAL alarm code -- not a description of it.

Downstream impact is reported as what the halt would have STOPPED, and pointedly
also what it would NOT have stopped, because a halt on the emitter does nothing
about work already merged.

    python tools/halt_shadow_report.py
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHADOW_DIR = os.path.join(ROOT, "artifacts", "lane_halts", "_shadow")
CENSUS_LATEST = os.path.join(ROOT, "artifacts", "queue_census", "latest.json")


def _load(name):
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(ROOT, "tools", name + ".py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m


def upstream(q) -> list:
    return [(l["name"], l["validator"]) for l in q.LANES]


def present(q) -> tuple:
    """Replay the CURRENT snapshot through the real alarm code."""
    if not os.path.isfile(CENSUS_LATEST):
        return None, []
    snap = json.load(open(CENSUS_LATEST, encoding="utf-8"))
    alarms = snap.get("alarms") or []
    would = [a for a in alarms if a["kind"] == "VALIDITY_COLLAPSE"]
    return snap, would


def retrospect(q) -> list:
    """The founding case, 2026-07-29: builder:manifest, 36 open, 0 valid.

    Run through q.alarms() rather than re-stated, so if the thresholds are ever
    loosened past this incident THIS REPORT GOES QUIET and that is the signal.
    """
    lane = {
        "name": "builder:manifest", "validator": "service_manifest",
        "depth": 36, "opened_24h": 36, "merged_24h": 0,
        "valid": 0, "checked": 36, "validity": 0.0,
        "invalid_examples": [{"pr": 2396, "why": "FLAT: no [service] header"},
                             {"pr": 2372, "why": "UNPARSEABLE: Python in a .toml"}],
        "silent_for": 0.4, "undrained_for": 11.5,
    }
    snap = {"at": "2026-07-29T22:00:00+00:00", "repo": "rob531/zo-sentinel",
            "validated": True, "open_total": 68, "lanes": [lane]}
    return [a for a in q.alarms(snap, None) if a["kind"] == "VALIDITY_COLLAPSE"]


def shadow_ledger() -> list:
    if not os.path.isdir(SHADOW_DIR):
        return []
    out = []
    for n in sorted(os.listdir(SHADOW_DIR)):
        if n.endswith(".json"):
            try:
                out.append(json.load(open(os.path.join(SHADOW_DIR, n), encoding="utf-8")))
            except (OSError, ValueError):
                pass
    return out


def main() -> int:
    q = _load("queue_census")
    print("\nHALT SHADOW REPORT   %s\n" % dt.datetime.now(dt.timezone.utc)
          .strftime("%Y-%m-%d %H:%MZ"))

    print("UPSTREAM -- which lanes can produce a halt at all")
    n_val = 0
    for name, v in upstream(q):
        if v:
            n_val += 1
        print("  %-20s %s" % (name, v or "-- no validator: CANNOT halt, arming is a no-op here"))
    print("  => %d of %d lanes are validated; the rest are counted, not judged.\n"
          % (n_val, len(upstream(q))))

    snap, would = present(q)
    print("PRESENT -- against the queue as it stands")
    if snap is None:
        print("  no census snapshot yet; run tools/queue_census.py first\n")
    else:
        print("  snapshot %s (%d open)" % (snap["at"][:16].replace("T", " "),
                                           snap.get("open_total", 0)))
        for l in snap["lanes"]:
            if l.get("validity") is not None:
                print("    %-20s validity=%.0f%% cohort=%d  %s"
                      % (l["name"], 100 * l["validity"], l["checked"],
                         "BELOW MIN_COHORT (%d) -- not judgeable"
                         % q.MIN_COHORT if l["checked"] < q.MIN_COHORT else ""))
        print("  => %d halt(s) would fire right now.%s\n"
              % (len(would),
                 "  Arming today is a NO-OP -- that is the useful fact." if not would else ""))

    print("RETROSPECT -- the founding case (2026-07-29: 36 open manifest PRs, 0 valid)")
    r = retrospect(q)
    if r:
        for a in r:
            print("  WOULD HAVE FIRED  [%s] %s" % (a["kind"], a["lane"]))
            print("     %s" % a["detail"])
            print("     -> %s" % a["action"])
    else:
        print("  *** WOULD NOT HAVE FIRED ***")
        print("  The detector misses the incident it was built for. Do NOT arm.")
    print()

    print("DOWNSTREAM -- what a halt does, and what it does not")
    print("  STOPS   : further emission on THAT lane only; siblings keep running.")
    print("  DOES NOT: unmerge, revert, or fix anything already landed. On 7/29 the")
    print("            7 defective manifests were already on main -- a halt would have")
    print("            prevented the next 36, not repaired the last 7.")
    print("  COST OF : a halt on a lane that was actually healthy stops real output;")
    print("  A FALSE   this is why MIN_COHORT exists and why the default stays shadow.")
    print("  POSITIVE")
    print()

    led = shadow_ledger()
    print("SHADOW LEDGER -- decisions recorded, acted on: none by construction")
    if not led:
        print("  empty (no VALIDITY_COLLAPSE has occurred since shadow mode landed)")
    for rec in led[-10:]:
        print("  %s  %-20s sha=%s\n     %s"
              % (rec["decided_at"][:19], rec["lane"],
                 rec.get("decided_on_sha") or "-", rec["reason"][:100]))
    print()
    print("VERDICT: %s" % ("detector reproduces its founding case; shadow ledger is the "
                           "evidence to arm on" if r else
                           "NOT READY -- founding case not reproduced"))
    return 0 if r else 1


if __name__ == "__main__":
    sys.exit(main())
