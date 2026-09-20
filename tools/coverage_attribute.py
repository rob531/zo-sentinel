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

  FLEET_QUIET  fleet activity that day was far below its own norm AND a
               second, independent store agrees -- this lane was one of many
               that did not run. Not a lane defect.
  UNCORROBORATED_QUIET
               the friction ledger was silent but the corroborating store shows
               the fleet working normally. The zero measures the RECORDER, not
               the fleet, so the gap has NO established cause.
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

CORROBORATION (added 2026-09-19, after this tool published 18 false verdicts)
---------------------------------------------------------------------------
The span guard above defends the ledger's EDGES. It does nothing for a zero
INSIDE the span -- and a zero inside the span has two causes: the fleet did not
run, or the fleet ran and nobody called the voluntary `friction.record`. Only
the first is a cause. Measured on 2026-09-19: the ledger showed 0/0/1 lanes on
2026-09-16/17/18 while 41/43/70 commits landed on origin/main, and 24-109
commits/day landed across the 14 August dates this tool had been calling quiet.
So every quiet claim is now defended by a second store written by a different
mechanism (git: a commit is a side effect of the work, not a voluntary call),
or it is not made.

EXIT CODES
    0  ran; every gap attributed or honestly UNKNOWN
    1  at least one gap needs a human read -- LANE_ALONE or
       UNCORROBORATED_QUIET (a reading, NOT an alert, NEVER an email)
    2  could not read an input store
"""

from __future__ import annotations

import argparse
import collections
import datetime as _dt
import json
import os
import statistics
import subprocess
import sys

STATE_PATH = r"D:\zo\runs\ops_audit_state.json"
FRICTION_PATH = r"D:\zo\Zocomputer Agents\friction_ledger.jsonl"
# Second, INDEPENDENT dated store used to corroborate a quiet claim. Chosen
# because git is written by a different mechanism than `friction.record`:
# a commit lands as a side effect of the work, while a friction row lands
# only if a lane voluntarily remembers to call the recorder.
GIT_REPO = r"D:\zo\_lanes\ops-audit"
GIT_REF = "origin/main"

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


def git_activity(repo: str = GIT_REPO, ref: str = GIT_REF):
    """Map date -> commit count on `ref`. The CORROBORATING store.

    Returns None (never raises, never {}) when git cannot speak -- an absent
    corroborator must degrade to "not corroborated", never to "corroborated
    quiet". Silence from the second store is R6 all over again.
    """
    try:
        out = subprocess.run(
            ["git", "log", ref, "--date=short", "--pretty=format:%ad"],
            cwd=repo, capture_output=True, text=True, timeout=120,
        )
    except Exception:  # noqa: BLE001
        return None
    if out.returncode != 0:
        return None
    counts = collections.Counter()
    for line in out.stdout.splitlines():
        line = line.strip()
        if len(line) == 10 and line[4] == "-":
            counts[line] += 1
    return dict(counts) or None


def attribute(missing_dates, by_day, quiet_fraction=QUIET_FRACTION_OF_MEDIAN,
              commits_by_day=None):
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
        row = {
            "date": date,
            "verdict": "FLEET_QUIET" if quiet else "LANE_ALONE",
            "lanes_active": active,
            "lanes": sorted(by_day.get(date, set())),
            "corroborated": None,
            "commits": None,
            "why": (f"{active} lane(s) active vs median {median:g} "
                    f"(quiet below {quiet_below:g})"),
        }

        # ------------------------------------------------------------------
        # R6 GUARD, ONE LEVEL IN (added 2026-09-19 after this tool published
        # 18 false FLEET_QUIET verdicts). Being INSIDE the ledger's span is
        # not the same as the ledger COVERING that date. A zero has two
        # causes -- the fleet did not run, or the fleet ran and nobody called
        # the voluntary recorder -- and only the first is a cause. Defend a
        # quiet claim with a second store or do not make it.
        # ------------------------------------------------------------------
        if quiet and commits_by_day:
            g_days = sorted(commits_by_day)
            g_median = statistics.median([commits_by_day[d] for d in g_days])
            g_quiet_below = g_median * quiet_fraction
            if date < g_days[0] or date > g_days[-1]:
                row["why"] += ("; NOT corroborated -- outside the corroborating "
                               f"store's span {g_days[0]}..{g_days[-1]}")
            else:
                c = commits_by_day.get(date, 0)
                row["commits"] = c
                if c < g_quiet_below:
                    row["corroborated"] = True
                    row["why"] += (f"; corroborated: {c} commit(s) on {GIT_REF} "
                                   f"vs median {g_median:g}")
                else:
                    row["corroborated"] = False
                    row["verdict"] = "UNCORROBORATED_QUIET"
                    row["why"] = (
                        f"friction ledger shows {active} lane(s), but {c} "
                        f"commit(s) landed on {GIT_REF} that day (median "
                        f"{g_median:g}). The zero is a RECORDING outage, not a "
                        "quiet fleet -- this gap has NO established cause.")
        elif quiet:
            row["why"] += "; NOT corroborated -- no second store available"
        results.append(row)
    return {
        "span": {"first": span_first, "last": span_last, "observed_days": len(observed)},
        "corroborator": (None if not commits_by_day else
                         {"store": f"git {GIT_REF}",
                          "first": min(commits_by_day),
                          "last": max(commits_by_day)}),
        "median_lanes_active": median,
        "quiet_below": quiet_below,
        "basis": (f"lanes-active per day from friction_ledger.jsonl over "
                  f"{span_first}..{span_last}; quiet threshold = "
                  f"{quiet_fraction:g} x median, derived at call time; every "
                  f"quiet claim corroborated against "
                  f"{'git ' + GIT_REF if commits_by_day else 'NOTHING'}"),
        "gaps": results,
    }



# A daily lane's contract is one run per day, so the largest gap that cadence
# permits is a SINGLE day. This is that contract expressed as a number, not a
# tuned constant: two or more CONSECUTIVE missing days is a BLACKOUT.
BLACKOUT_MIN_DAYS = 2
# A REPORTING window, not a threshold on measured data: it asks whether the
# most recent blackout ended recently enough to mean "this lane is failing
# now" rather than "this lane had a bad month".
RECENT_WINDOW_DAYS = 7


def streaks(missing_dates, last_entry_date=None,
            min_days=BLACKOUT_MIN_DAYS, recent_window=RECENT_WINDOW_DAYS):
    """Collapse a flat gap list into dated CONSECUTIVE runs.

    WHY (measured 2026-09-20): `missing_dates` is published flat and bucketed
    only by cause, so a reader cannot tell a lane that died THIS WEEK from a
    lane that had a bad month two months ago. The live list that day held
    2026-09-16/17/18 -- three consecutive days, ended two days earlier -- and
    the headline "21 missing across 57 days" said nothing about it. This lane
    is the ONLY writer of entries[], so a consecutive run is a window in which
    the paid-GPU balance went unread and a stranded rented instance could have
    burned unobserved.

    Everything is dated against `last_entry_date` -- the last day the lane
    demonstrably DID run -- never against wall clock, so the reading is
    deterministic and testable.
    """
    dates = sorted({d[:10] for d in missing_dates if d})
    runs = []
    for iso in dates:
        day = _dt.date.fromisoformat(iso)
        if runs and day - _dt.date.fromisoformat(runs[-1][-1]) == _dt.timedelta(days=1):
            runs[-1].append(iso)
        else:
            runs.append([iso])

    ref = _dt.date.fromisoformat(last_entry_date) if last_entry_date else None
    blackouts = []
    for run in runs:
        if len(run) < min_days:
            continue
        row = {"start": run[0], "end": run[-1], "days": len(run), "days_ago": None}
        if ref is not None:
            row["days_ago"] = (ref - _dt.date.fromisoformat(run[-1])).days
        blackouts.append(row)

    most_recent = blackouts[-1] if blackouts else None
    recent = bool(most_recent
                  and most_recent["days_ago"] is not None
                  and most_recent["days_ago"] <= recent_window)
    return {
        "blackouts": blackouts,
        "longest_days": max((len(r) for r in runs), default=0),
        "single_day_gaps": sum(1 for r in runs if len(r) == 1),
        "most_recent": most_recent,
        "recent_blackout": recent,
        "reference_date": last_entry_date,
        "basis": ("consecutive runs within coverage.missing_dates; a run of "
                  "%d+ days is a BLACKOUT (a daily lane's cadence permits a "
                  "gap of one day, so anything longer is unobserved time); "
                  "days_ago is counted from %s, the last day this lane "
                  "demonstrably ran, never from wall clock; RECENT means the "
                  "most recent blackout ended within %d day(s) of that date"
                  % (min_days, last_entry_date or "UNKNOWN", recent_window)),
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

    # Pole 4 (THE MOTIVATING INCIDENT, 2026-09-19): a day the friction ledger
    # calls empty while the fleet shipped 41-70 commits. Measured live: the
    # ledger had 0 lanes on 2026-09-16/17 and 1 on 09-18; git had 41/43/70.
    # Before this branch existed all three published as a confident
    # FLEET_QUIET -- a cause manufactured out of an unwritten store.
    busy_git = {"2026-09-%02d" % d: n
                for d, n in [(1, 40), (2, 45), (3, 50), (4, 44), (5, 17),
                             (6, 14), (7, 31), (8, 24), (9, 52), (10, 92),
                             (11, 37), (12, 69), (13, 59), (14, 67), (15, 39),
                             (16, 41), (17, 43), (18, 70)]}
    by_day4 = dict(by_day)
    by_day4["2026-09-16"] = set()
    by_day4["2026-09-17"] = set()
    got4 = attribute(["2026-09-16", "2026-09-17"], by_day4, commits_by_day=busy_git)
    for g in got4["gaps"]:
        if g["verdict"] != "UNCORROBORATED_QUIET":
            failures.append("%s: ledger-zero on a %s-commit day must be "
                            "UNCORROBORATED_QUIET, got %s"
                            % (g["date"], busy_git[g["date"]], g["verdict"]))
        if g["corroborated"] is not False:
            failures.append("%s: corroborated must be False, got %r"
                            % (g["date"], g["corroborated"]))

    # Pole 5 (NEGATIVE CONTROL for pole 4): the SAME ledger-zero day, but the
    # corroborator agrees the fleet was down. This MUST stay FLEET_QUIET --
    # otherwise the new branch is not a detector, it is a blanket refusal that
    # would score identically on every input.
    quiet_git = dict(busy_git)
    quiet_git["2026-09-16"] = 0
    quiet_git["2026-09-17"] = 1
    got5 = attribute(["2026-09-16", "2026-09-17"], by_day4, commits_by_day=quiet_git)
    for g in got5["gaps"]:
        if g["verdict"] != "FLEET_QUIET":
            failures.append("%s: ledger-zero + git-zero must stay FLEET_QUIET, "
                            "got %s" % (g["date"], g["verdict"]))
        if g["corroborated"] is not True:
            failures.append("%s: corroborated must be True, got %r"
                            % (g["date"], g["corroborated"]))

    # Pole 6: a SILENT corroborator must not manufacture corroboration. An
    # absent second store degrades to "not corroborated", never to "quiet".
    got6 = attribute(["2026-09-16"], by_day4, commits_by_day=None)
    g6 = got6["gaps"][0]
    if g6["verdict"] != "FLEET_QUIET" or g6["corroborated"] is not None:
        failures.append("silent corroborator must give FLEET_QUIET/corroborated=None, "
                        "got %s/%r" % (g6["verdict"], g6["corroborated"]))
    if "NOT corroborated" not in g6["why"]:
        failures.append("silent corroborator must SAY it did not corroborate")

    # Pole 7: a date outside the CORROBORATOR's span is not corroborated
    # either -- the same span discipline the primary store already gets.
    got7 = attribute(["2026-09-16"], by_day4,
                     commits_by_day={"2026-09-20": 5, "2026-09-21": 6})
    g7 = got7["gaps"][0]
    if g7["corroborated"] is not None or "outside the corroborating" not in g7["why"]:
        failures.append("pre-span corroborator date must be uncorroborated, got %r/%s"
                        % (g7["corroborated"], g7["why"]))


    # ------------------------------------------------------------------
    # RECENCY POLES (added 2026-09-20). A flat list of 21 dates reads the
    # same whether this lane died yesterday or had a bad August. It did
    # not: 2026-09-16/17/18 is a three-day consecutive blackout that ended
    # two days before this was written. The shape was in every prior run's
    # output; nothing computed it, so nobody read it.
    # ------------------------------------------------------------------
    checks = 13

    # Pole 8 (THE MOTIVATING INCIDENT, the REAL gap list observed live on
    # 2026-09-20 -- not a fixture invented to agree with the code).
    live = ["2026-07-31", "2026-08-14", "2026-08-15", "2026-08-16",
            "2026-08-17", "2026-08-18", "2026-08-19", "2026-08-20",
            "2026-08-21", "2026-08-22", "2026-08-25", "2026-08-26",
            "2026-08-27", "2026-08-28", "2026-08-29", "2026-08-30",
            "2026-09-07", "2026-09-08", "2026-09-16", "2026-09-17",
            "2026-09-18"]
    s8 = streaks(live, last_entry_date="2026-09-20")
    checks += 4
    recent8 = s8["most_recent"]
    if not recent8 or recent8["end"] != "2026-09-18" or recent8["days"] != 3:
        failures.append("live gap list: most recent blackout should be 3 days "
                        "ending 2026-09-18, got %r" % (recent8,))
    if not recent8 or recent8["days_ago"] != 2:
        failures.append("a blackout must be dated against the last OBSERVED "
                        "run (expected days_ago=2), got %r"
                        % (recent8 or {}).get("days_ago"))
    if not s8["recent_blackout"]:
        failures.append("a blackout that ended 2 days ago must read as RECENT")
    if s8["longest_days"] != 9:
        failures.append("longest run 2026-08-14..08-22 is 9 days, got %s"
                        % s8["longest_days"])

    # Pole 9 (NEGATIVE CONTROL): the SAME NUMBER of gaps, none consecutive.
    # A flag that fires here too is not a detector, it is a constant -- the
    # failure mode of every guard this ledger has had to retract.
    scattered = (["2026-08-%02d" % d for d in range(1, 30, 2)][:11]
                 + ["2026-07-%02d" % d for d in range(1, 21, 2)][:10])
    s9 = streaks(sorted(scattered), last_entry_date="2026-09-20")
    checks += 3
    if s9["blackouts"]:
        failures.append("21 NON-consecutive gaps must yield no blackout, got %d"
                        % len(s9["blackouts"]))
    if s9["recent_blackout"]:
        failures.append("scattered single-day gaps must not read as a blackout")
    if s9["single_day_gaps"] != len(scattered):
        failures.append("every scattered gap should count as a single-day gap, "
                        "got %s of %s" % (s9["single_day_gaps"], len(scattered)))

    # Pole 10 (NEGATIVE CONTROL for the RECENT branch): an old blackout is
    # still a blackout but must NOT read as recent. Without this assertion
    # the recent flag would be decoration -- true of every blackout ever.
    s10 = streaks(["2026-01-10", "2026-01-11", "2026-01-12"],
                  last_entry_date="2026-09-20")
    checks += 2
    if len(s10["blackouts"]) != 1 or s10["blackouts"][0]["days"] != 3:
        failures.append("an old 3-day run is still a blackout, got %r"
                        % s10["blackouts"])
    if s10["recent_blackout"]:
        failures.append("a blackout 250+ days old must not read as RECENT")

    # Pole 11 (BOUNDARY): one isolated missing day is a daily lane's
    # permitted gap, not a blackout.
    s11 = streaks(["2026-09-18"], last_entry_date="2026-09-20")
    checks += 2
    if s11["blackouts"]:
        failures.append("a single missing day is not a blackout")
    if s11["longest_days"] != 1:
        failures.append("longest_days must still report the 1-day run, got %s"
                        % s11["longest_days"])

    # Pole 12: no gaps at all must be silent, not a zero-length blackout.
    s12 = streaks([], last_entry_date="2026-09-20")
    checks += 1
    if s12["blackouts"] or s12["longest_days"] or s12["recent_blackout"]:
        failures.append("an empty gap list must produce no blackout reading, "
                        "got %r" % s12)

    for f in failures:
        print("FAIL: %s" % f)
    if failures:
        print("\nself-test FAILED (%d assertion(s))" % len(failures))
        return 1
    print("self-test PASSED (%d assertions): motivating incident split correctly, "
          "pre-span date refused (R6), threshold tracks the population, "
          "a ledger-zero on a busy-git day refuses to claim quiet, a "
          "corroborated quiet day still passes, and a silent or out-of-span "
          "corroborator never manufactures corroboration; and a "
          "multi-day blackout is seen, dated, and told apart from "
          "scattered single-day gaps" % checks)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true", help="emit JSON only")
    ap.add_argument("--self-test", action="store_true", help="R4 both-poles control")
    ap.add_argument("--state", default=STATE_PATH)
    ap.add_argument("--friction", default=FRICTION_PATH)
    ap.add_argument("--git-repo", default=GIT_REPO,
                    help="repo whose commit history corroborates a quiet claim")
    ap.add_argument("--no-corroborate", action="store_true",
                    help="skip the second store (every quiet day then reports "
                         "corroborated=None, never corroborated=True)")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    try:
        missing, cov = load_missing_dates(args.state)
        by_day = fleet_activity(args.friction)
    except InputUnreadable as exc:
        print("UNREADABLE: %s" % exc, file=sys.stderr)
        return 2

    commits = None if args.no_corroborate else git_activity(args.git_repo)
    out = attribute(missing, by_day, commits_by_day=commits)
    out["coverage"] = {
        "observed_days": cov.get("observed_days"),
        "first_entry_date": cov.get("first_entry_date"),
        "last_entry_date": cov.get("last_entry_date"),
        "missing_count": len(missing),
    }
    out["streaks"] = streaks(missing, last_entry_date=cov.get("last_entry_date"))

    if args.json:
        print(json.dumps(out, indent=2))
    else:
        c = out["coverage"]
        print("COVERAGE GAPS  %s missing across %s..%s (%s days observed)"
              % (c["missing_count"], c["first_entry_date"],
                 c["last_entry_date"], c["observed_days"]))
        print("  basis: %s" % out["basis"])
        if out.get("corroborator"):
            print("  corroborator: %s (%s..%s)"
                  % (out["corroborator"]["store"], out["corroborator"]["first"],
                     out["corroborator"]["last"]))
        else:
            print("  corroborator: NONE -- no quiet claim here is corroborated")
        tally = collections.Counter(g["verdict"] for g in out["gaps"])
        for verdict in ("LANE_ALONE", "UNCORROBORATED_QUIET", "FLEET_QUIET", "UNKNOWN"):
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
        s = out["streaks"]
        if s["blackouts"]:
            print("\n  BLACKOUTS  (%d run(s) of %d+ consecutive days)"
                  % (len(s["blackouts"]), BLACKOUT_MIN_DAYS))
            for b in s["blackouts"]:
                mark = ""
                if s["recent_blackout"] and b is s["most_recent"]:
                    mark = ("   <-- RECENT: blind %d day(s) before the last "
                            "observed run" % b["days_ago"])
                print("    %s..%s  %d day(s), ended %s day(s) before the last "
                      "observed run%s" % (b["start"], b["end"], b["days"],
                                          b["days_ago"], mark))
            print("  longest consecutive run %d day(s); %d isolated single-day "
                  "gap(s)" % (s["longest_days"], s["single_day_gaps"]))
            print("  basis: %s" % s["basis"])
        else:
            print("\n  BLACKOUTS  none -- every gap is an isolated single day "
                  "(%d of them)" % s["single_day_gaps"])
        if s["recent_blackout"]:
            print("\n  A RECENT BLACKOUT IS THE COVERAGE READING A FLAT DATE "
                  "LIST HIDES: this lane is the ONLY writer of entries[], so a "
                  "consecutive run is a window in which the paid-GPU balance "
                  "went unread and a stranded rented instance could have "
                  "burned unobserved. Still a READING -- not an alert, never "
                  "an email.")
        if tally["UNCORROBORATED_QUIET"]:
            print("\n  UNCORROBORATED_QUIET is not FLEET_QUIET: the friction "
                  "ledger was silent while the fleet shipped commits, so the "
                  "zero measures the RECORDER, not the fleet. These gaps have "
                  "no established cause and must not be written off as quiet.")

    needs_read = ("LANE_ALONE", "UNCORROBORATED_QUIET")
    needs = any(g["verdict"] in needs_read for g in out["gaps"])
    return 1 if (needs or out["streaks"]["recent_blackout"]) else 0


if __name__ == "__main__":
    sys.exit(main())
