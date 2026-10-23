#!/usr/bin/env python3
"""builder_selftest_integrity_report.py -- the FU-031 probe (READ-ONLY).

Measures the acceptance-self-test integrity of builder-lane builds by parsing
goose_runner.log. It is the OBSERVE half that must clear before the SOA builder
emission (goose_recipes/service_dir_from_exemplar.yaml) or the staged->active
promotion gate can be trusted: if the acceptance self-test degrades to Tier-0
most of the time, "the contract passed" means nothing, and the SOA promotion
gate (which runs contract.py) would inherit the same blindness.

It changes NOTHING and blocks NOTHING (Harness-Engineering: observe before
enforce, the same shape as the reachability ratchet). Its whole value is
GROUPING degradations/failures by their verbatim root cause, so one shared cause
across many modules shows up as ONE bucket -- FU-031(a) suspects a single shared
import/env/seed-kwarg cause behind the bulk of the degradation.

Line shapes parsed (from live goose_runner.log):
Line shapes parsed -- CURRENT three-state contract emitted by goose_runner.py
(see tests/test_tier0_selftest_logging.py, which asserts the emitter's side):
  [selftest] <t>: self-test PASS
  [selftest] <t>: self-test RED (<reason>) -- blocking completion :: <tail>
  [selftest] <t>: self-test UNKNOWN (<reason>) -- could not evaluate,
                  degrading to Tier-0 (not blocking) :: <tail>
  [selftest] <t>: could not run (<Type>: <msg>) -- Tier-0 only
  [ghost-guard] <t>: goose reported success but ... rejected by gate 'selftest' ...
  [engine] repair N/M for <file>: self-test did not PASS (rc=1): <tail>
LEGACY shapes, kept so historical logs still parse (FU-196):
  [selftest] <t>: self-test FAILED -- blocking completion :: <tail>
  [selftest] <t>: import/env failure -- degrading to Tier-0 (not blocking) :: <tail>

FU-196 (2026-07-30): the emitter moved to the RED/UNKNOWN/PASS contract and this
probe kept grepping `self-test FAILED` / `import/env failure -- degrading`.
Neither string existed in the live log any more, so 448 RED events matched NO
branch: they fell out of BOTH the numerator and the denominator and the probe
published `degradation 0% / pass-rate 100%` over hundreds of blocking failures.
The durable guard against a THIRD vocabulary drift is `unrecognised`: every
`[selftest] <t>:` line that matches no branch is counted and surfaced, so the
next drift reads as "this report cannot be trusted" instead of as good news
(HARNESS_DOCTRINE R3 -- a bucket that went to zero must prove the check RAN;
R6 -- unknown != zero).

For each degrade/fail, the first DISTINCTIVE error line within the next few log
lines is used as the cause-bucket key (unknown column / cannot import name /
NameError / no such column / kwarg ...).

    python tools/builder_selftest_integrity_report.py                 # all history
    python tools/builder_selftest_integrity_report.py --since-hours 3 # trend window
    python tools/builder_selftest_integrity_report.py --json
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone

DEFAULT_LOG = os.environ.get("GOOSE_RUNNER_LOG", "/home/workspace/logs/goose_runner.log")

TS = re.compile(r"^\[(\d{4}-\d{2}-\d{2}T[\d:.+-]+)\]")
# UNKNOWN / could-not-run / legacy import-env are all "the acceptance test did not
# EVALUATE" -- they belong in the degraded bucket, never in pass (R6: unknown != zero).
DEGRADE = re.compile(
    r"\[selftest\]\s+(\S+?):\s+"
    r"(?:import/env failure -- degrading to Tier-0"           # legacy vocabulary
    r"|self-test UNKNOWN\b[^\n]*?degrading to Tier-0"          # current three-state contract
    r"|could not run \([^\n]*?\) -- Tier-0 only)"              # harness never started
)
PASS = re.compile(r"\[selftest\]\s+(\S+?):\s+self-test PASS")
# RED is the current word for a self-test that RAN and FAILED; FAILED is the legacy word.
FAILED = re.compile(r"\[selftest\]\s+(\S+?):\s+self-test (?:RED|FAILED)\b")
# any [selftest] line at all -- the drift detector's denominator
SELFTEST_ANY = re.compile(r"\[selftest\]\s+(\S+?):\s*(.*)$")
GHOST = re.compile(r"\[ghost-guard\]\s+(\S+?):")
REPAIR = re.compile(r"\[engine\]\s+repair\s+\d+/\d+\s+for\s+(\S+?):")
# distinctive root-cause signatures to bucket on
CAUSE = re.compile(
    r"(unknown column kwarg '[^']+'"
    r"|cannot import name '[^']+'"
    r"|no such column[:\s].*"
    r"|NameError:.*"
    r"|ModuleNotFoundError:.*"
    r"|ImportError:.*"
    r"|AttributeError:.*"
    r"|IntegrityError.*"
    r"|OperationalError.*"
    r"|TypeError:.*)")


def _parse_ts(line):
    m = TS.match(line)
    if not m:
        return None
    try:
        return datetime.fromisoformat(m.group(1))
    except ValueError:
        return None


def analyze(lines, since=None):
    passed, degraded, failed_blocking = [], [], []
    ghost_rejects, repairs, unrecognised = [], [], []
    cause_buckets = defaultdict(list)   # cause -> [targets]
    per_target = []

    def _in_window(ts):
        return since is None or (ts is not None and ts >= since)

    for i, line in enumerate(lines):
        ts = _parse_ts(line)
        def _cause_after():
            for j in range(i, min(i + 16, len(lines))):
                c = CAUSE.search(lines[j])
                if c:
                    return c.group(1).strip()[:160]
            tail = line.split("::", 1)[-1].strip()
            return (tail[:160] or "unclassified")

        m = DEGRADE.search(line)
        if m and _in_window(ts):
            degraded.append(m.group(1)); cause_buckets[_cause_after()].append(m.group(1))
            per_target.append({"target": m.group(1), "outcome": "tier0_degraded", "ts": ts and ts.isoformat()})
            continue
        m = PASS.search(line)
        if m and _in_window(ts):
            passed.append(m.group(1))
            per_target.append({"target": m.group(1), "outcome": "pass", "ts": ts and ts.isoformat()})
            continue
        m = FAILED.search(line)
        if m and _in_window(ts):
            failed_blocking.append(m.group(1)); cause_buckets[_cause_after()].append(m.group(1))
            per_target.append({"target": m.group(1), "outcome": "failed_blocking", "ts": ts and ts.isoformat()})
            continue
        # FU-196 drift detector: a [selftest] line that matched none of the branches
        # above is a VOCABULARY we do not know. It must not vanish silently -- that is
        # exactly how 448 blocking failures got published as a 0% degradation rate.
        m = SELFTEST_ANY.search(line)
        if m and _in_window(ts):
            unrecognised.append({"target": m.group(1), "text": m.group(2).strip()[:120],
                                 "ts": ts and ts.isoformat()})
            continue
        m = GHOST.search(line)
        if m and _in_window(ts):
            ghost_rejects.append(m.group(1)); continue
        m = REPAIR.search(line)
        if m and _in_window(ts):
            repairs.append(m.group(1)); cause_buckets[_cause_after()].append(m.group(1))

    ran = len(passed) + len(failed_blocking)          # self-tests that actually EXECUTED
    total_events = ran + len(degraded)                 # execution attempts (incl. degraded)
    degradation_rate = (len(degraded) / total_events) if total_events else 0.0
    ran_pass_rate = (len(passed) / ran) if ran else 0.0

    buckets = sorted(
        ({"cause": c, "count": len(t), "distinct_targets": sorted(set(t))[:12]}
         for c, t in cause_buckets.items()),
        key=lambda b: b["count"], reverse=True)

    return {
        "selftest_pass": len(passed),
        "selftest_failed_blocking": len(failed_blocking),
        "tier0_degraded": len(degraded),
        "executed": ran,
        "attempts": total_events,
        "degradation_rate": round(degradation_rate, 3),
        "executed_pass_rate": round(ran_pass_rate, 3),
        "ghost_guard_rejections": len(ghost_rejects),
        "engine_repairs": len(repairs),
        "shared_cause_buckets": buckets,
        "distinct_causes": len(buckets),
        "per_target_tail": per_target[-25:],
        # FU-196: >0 means the emitter speaks a vocabulary this probe does not parse,
        # so every number above is an UNDERCOUNT and must be read as unmeasured.
        "unrecognised_selftest_lines": len(unrecognised),
        "unrecognised_samples": [u["text"] for u in unrecognised[:5]],
        "trustworthy": not unrecognised,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="FU-031 probe: builder self-test integrity (read-only).")
    ap.add_argument("--log", default=DEFAULT_LOG)
    ap.add_argument("--since-hours", type=float, default=None)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    since = None
    if args.since_hours is not None:
        from datetime import timedelta
        since = datetime.now(timezone.utc) - timedelta(hours=args.since_hours)

    try:
        with open(args.log, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
    except OSError:
        lines = []

    rep = analyze(lines, since)
    rep["window"] = ("last %sh" % args.since_hours) if args.since_hours else "all history"
    rep["log"] = args.log

    if args.json:
        print(json.dumps(rep, indent=2))
        return 0

    print("=== builder self-test integrity (FU-031 probe, %s) ===" % rep["window"])
    if not rep["trustworthy"]:
        print("  !! UNTRUSTWORTHY: %d [selftest] line(s) matched NO known shape."
              % rep["unrecognised_selftest_lines"])
        print("  !! The emitter's vocabulary has drifted; every number below is an")
        print("  !! UNDERCOUNT and must be reported as UNMEASURED, not as zero (FU-196).")
        for s in rep["unrecognised_samples"]:
            print("  !!   unparsed: %s" % s)
    print("  executed: %d  (pass %d / failed-blocking %d)  |  tier0-degraded: %d"
          % (rep["executed"], rep["selftest_pass"], rep["selftest_failed_blocking"], rep["tier0_degraded"]))
    print("  DEGRADATION RATE: %.0f%%   (acceptance self-test skipped, not run)"
          % (100 * rep["degradation_rate"]))
    print("  executed pass-rate: %.0f%%  | ghost-guard rejects: %d | engine repairs: %d"
          % (100 * rep["executed_pass_rate"], rep["ghost_guard_rejections"], rep["engine_repairs"]))
    print("\n  SHARED ROOT-CAUSE BUCKETS (FU-031(a): one cause, many modules):")
    for b in rep["shared_cause_buckets"][:8]:
        print("    x%-3d  %s" % (b["count"], b["cause"]))
        print("           -> %s" % ", ".join(b["distinct_targets"][:6]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
