#!/usr/bin/env python3
"""Attribute each ops_audit coverage gap to a CAUSE, instead of leaving it bare.

WHY THIS EXISTS
---------------
`ops_audit_state.py show` emits `coverage.missing_dates`: dates on which this
lane did not run, recorded by the work itself (FU-207 class -- beats `lastRunAt`,
which can advance for a slot that did no work).

The daily SKILL then instructs the operator to "check whether a sibling lane
already recorded the cause before treating it as new". Until now there was no
tool for that step, so every run that saw a gap either hand-rolled the
archaeology (three ad-hoc calls, measured 2026-09-12) or -- far more likely --
skipped it and published a bare list of dates. A manual step on the daily path
is a step that silently stops happening.

WHAT IT DOES
------------
For each missing date it reads the FLEET-WIDE friction ledger and counts how
many DISTINCT lanes were active that day, then splits the gap three ways:

  FLEET_QUIET  fleet activity that day was far below its own norm -- this lane
               was one of many that did not run. Not a lane defect.
  LANE_ALONE   fleet activity was normal and this lane alone is missing. That
               is the reading worth investigating.
  UNKNOWN      the date lies outside the friction ledger's observed span, so
               the ledger CANNOT speak to it.

THE UNKNOWN BRANCH IS THE POINT (R6: unknown is not zero)
---------------------------------------------------------
A date before the ledger's first row yields zero active lanes from a store that
never covered it. Reporting that as FLEET_QUIET would manufacture a confident
cause out of an unreadable window -- precisely the false-zero class this ledger
keeps paying for. So the span check runs BEFORE the count is interpreted, and a
date outside the span can never be attributed.

NO STATIC THRESHOLD
-------------------
"Far below its own norm" is derived from the observed distribution at call time
(a fraction of the median active-lane count), never a literal. A constant cannot
guard a growing population: the fleet was a handful of lanes once and is 21 now,
and any number written here today would silently mean something different later.

USAGE
    python tools\\coverage_attribute.py                 # attribute live gaps
    python tools\\coverage_attribute.py --json
    python tools\\coverage_attribute.py --self-test     # R4 both-poles control

EXIT CODES
    0  ran; every gap attributed or honestly UNKNOWN
    1  at least one gap classified LANE_ALONE (a reading, NOT an alert)
    2  could not read an input store
"""

from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import os
import statistics
import sys

STATE_PATH = r"D:\zo\runs\ops_audit_state.json"
FRICTION_PATH = r"D:\zo\Zocomputer Agents\friction_ledger.jsonl"

# Fraction of the median active-lane count below which a day counts as quiet.
# Derived-from-distribution, not an absolute lane count -- see module docstring.
QUIET_FRACTION_OF_MEDIAN = 0.5


class InputUnreadable(RuntimeError):
    pass


def load_missing_dates(state_path: str = STATE_PATH):
    """Return (missing_dates, coverage_block) from the ops audit state file."""
    try:
        with open(state_path, encoding="utf-8-sig") as fh:
            state = json.load(fh)
    except Exception as exc:  # noqa: BLE001
        raise InputUnreadable(f"cannot read state {state_path}: {exc}") from exc

    cov = state.get("coverage")
    if not isinstance(cov, dict):
        # schema v2 stores entries[]; recompute the gap list from them rather
        # than assuming a cached coverage block exists.
        entries = state.get("entries") or []
        dates = sorted({(e.get("date") or "")[:10] for e in entries if e.get("date")})
        if not dates:
            raise InputUnreadable(f"no entries[] or coverage in {state_path}")
        cov = _coverage_from_dates(dates)
    return list(cov.get("missing_dates") or []), cov


def _coverage_from_dates(dates):
    first, last = dates[0], dates[-1]
    d0 = _dt.date.fromisoformat(first)
    d1 = _dt.date.fromisoformat(last)
    have = set(dates)
    missing = []
    cur = d0
    while cur <= d1:
        iso = cur.isoformat()
        if iso not in have:
            missing.append(iso)
        cur += _dt.timedelta(days=1)
    return {
        "scope": "all",
        "observed_days": len(have),
        "first_entry_date": first,
        "last_entry_date": last,
        "missing_dates": missing,
        "basis": "recomputed from entries[] by coverage_attribute",
    }


def fleet_activity(friction_path: str = FRICTION_PATH):
    """Map date -> set of lanes that recorded a friction row that day."""
    if not os.path.isfile(friction_path):
        raise InputUnreadable(f"cannot read friction ledger {friction_path}")
    by_day = collections.defaultdict(set)
    with open(friction_path, encoding="utf-8-sig") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:  # noqa: BLE001
                continue  # a malformed row is not a reason to lose the file
            ts = row.get("at") or row.get("ts") or ""
            lane = row.get("lane") or row.get("lane_name")
            if lane and isinstance(ts, str) and len(ts) >= 10:
                by_day[ts[:10]].add(lane)
    if not by_day:
        raise InputUnreadable(f"friction ledger {friction_path} yielded no dated rows")
    return dict(by_day)


def attribute(missing_dates, by_day, quiet_fraction=QUIET_FRACTION_OF_MEDIAN):
    """Classify each missing date. Pure function -- unit-testable without I/O."""
    observed = sorted(by_day)
    span_first, span_last = observed[0], observed[-1]
    counts = [len(by_day[d]) for d in observed]
    median = statistics.median(counts)
    quiet_below = median * quiet_fraction

    results = []
    for date in missing_dates:
        # R6 GUARD -- runs BEFORE the count is interpreted. A date the ledger
        # never covered yields 0 lanes from ignorance, not from quiet.
        if date < span_first or date > span_last:
            results.append({
                "date": date,
                "verdict": "UNKNOWN",
                "lanes_active": None,
                "why": (f"outside friction-ledger span {span_first}..{span_last}; "
                        "the store cannot speak to this date"),
            })
            continue
        active = len(by_day.get(date, set()))
        quiet = active < quiet_below
        results.append({
            "date": date,
            "verdict": "FLEET_QUIET" if quiet else "LANE_ALONE",
            "lanes_active": active,
            "lanes": sorted(by_day.get(date, set())),
            "why": (f"{active} lane(s) active vs median {median:g} "
                    f"(quiet below {quiet_below:g})"),
        })
    return {
        "span": {"first": span_first, "last": span_last, "observed_days": len(observed)},
        "median_lanes_active": median,
        "quiet_below": quiet_below,
        "basis": (f"lanes-active per day from friction_ledger.jsonl over "
                  f"{span_first}..{span_last}; quiet threshold = "
                  f"{quiet_fraction:g} x median, derived at call time"),
        "gaps": results,
    }


# --------------------------------------------------------------------------
# R4: a detector's first proof is the incident that motivated it, plus a
# control that MUST trip. Fixtures agree with the code that wrote them, so
# both poles here are anchored on real observed shapes.
# --------------------------------------------------------------------------

def self_test() -> int:
    failures = []

    # Pole 1 (POSITIVE / the motivating incident): a genuinely quiet fleet day
    # and a normal day must classify DIFFERENTLY. Shapes taken from the
    # 2026-09-07 (2 lanes) / 2026-09-08 (5 lanes) observation, median ~9.
    by_day = {"2026-09-%02d" % d: {"lane%d" % i for i in range(n)}
              for d, n in [(1, 9), (2, 11), (3, 13), (4, 12), (5, 8),
                           (6, 9), (7, 2), (8, 5), (9, 10), (10, 9),
                           (11, 11), (12, 6)]}
    # 07 and 08 are present in the fleet ledger but absent from OUR entries[].
    got = attribute(["2026-09-07", "2026-09-08"], by_day)
    verdicts = {g["date"]: g["verdict"] for g in got["gaps"]}
    if verdicts.get("2026-09-07") != "FLEET_QUIET":
        failures.append("09-07 (2 lanes) should be FLEET_QUIET, got %s" % verdicts.get("2026-09-07"))
    if verdicts.get("2026-09-08") != "LANE_ALONE":
        failures.append("09-08 (5 lanes) should be LANE_ALONE, got %s" % verdicts.get("2026-09-08"))

    # Pole 2 (NEGATIVE CONTROL): a date OUTSIDE the ledger span must be
    # UNKNOWN and must NOT be called quiet. Without this branch the tool
    # would report every pre-ledger date as a confident FLEET_QUIET -- the
    # exact false-zero it was built to avoid. Observed RED by deleting the
    # span check: 2026-07-31 then classifies FLEET_QUIET on 0 lanes.
    got2 = attribute(["2026-07-31"], by_day)
    g2 = got2["gaps"][0]
    if g2["verdict"] != "UNKNOWN":
        failures.append("pre-span date must be UNKNOWN, got %s" % g2["verdict"])
    if g2["lanes_active"] is not None:
        failures.append("pre-span date must not publish a lane count")

    # Pole 3: the threshold must MOVE with the population, not sit at a literal.
    small = {"2026-01-%02d" % d: {"l%d" % i for i in range(n)}
             for d, n in [(1, 4), (2, 4), (3, 4), (4, 1)]}
    g3 = attribute(["2026-01-04"], small)["gaps"][0]
    if g3["verdict"] != "FLEET_QUIET":
        failures.append("small-fleet quiet day should be FLEET_QUIET, got %s" % g3["verdict"])
    if attribute(["2026-09-08"], by_day)["quiet_below"] == attribute(["2026-01-04"], small)["quiet_below"]:
        failures.append("threshold did not move with the population -- it is effectively a constant")

    for f in failures:
        print("FAIL: %s" % f)
    if failures:
        print("\nself-test FAILED (%d assertion(s))" % len(failures))
        return 1
    print("self-test PASSED: motivating incident split correctly, "
          "pre-span date refused (R6), threshold tracks the population")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    ap.add_argument("--self-test", action="store_true", help="R4 both-poles control")
    ap.add_argument("--state", default=STATE_PATH)
    ap.add_argument("--friction", default=FRICTION_PATH)
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    try:
        missing, cov = load_missing_dates(args.state)
        by_day = fleet_activity(args.friction)
    except InputUnreadable as exc:
        print("UNREADABLE: %s" % exc, file=sys.stderr)
        return 2

    out = attribute(missing, by_day)
    out["coverage"] = {
        "observed_days": cov.get("observed_days"),
        "first_entry_date": cov.get("first_entry_date"),
        "last_entry_date": cov.get("last_entry_date"),
        "missing_count": len(missing),
    }

    if args.json:
        print(json.dumps(out, indent=2))
    else:
        c = out["coverage"]
        print("COVERAGE GAPS  %s missing across %s..%s (%s days observed)"
              % (c["missing_count"], c["first_entry_date"],
                 c["last_entry_date"], c["observed_days"]))
        print("  basis: %s" % out["basis"])
        tally = collections.Counter(g["verdict"] for g in out["gaps"])
        for verdict in ("LANE_ALONE", "FLEET_QUIET", "UNKNOWN"):
            rows = [g for g in out["gaps"] if g["verdict"] == verdict]
            if not rows:
                continue
            print("\n  %s  (%d)" % (verdict, tally[verdict]))
            for g in rows:
                extra = "" if g["lanes_active"] is None else "  [%d lane(s)]" % g["lanes_active"]
                print("    %s%s  -- %s" % (g["date"], extra, g["why"]))
        if tally["UNKNOWN"]:
            print("\n  UNKNOWN is not zero: those dates predate the friction "
                  "ledger and carry no cause either way.")

    return 1 if any(g["verdict"] == "LANE_ALONE" for g in out["gaps"]) else 0


if __name__ == "__main__":
    sys.exit(main())
