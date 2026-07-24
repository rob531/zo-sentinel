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
  [selftest] <t>: import/env failure -- degrading to Tier-0 (not blocking) :: <tail>
  [selftest] <t>: self-test PASS
  [selftest] <t>: self-test FAILED -- blocking completion :: <tail>
  [ghost-guard] <t>: goose reported success but ... rejected by gate 'selftest' ...
  [engine] repair N/M for <file>: self-test did not PASS (rc=1): <tail>
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
DEGRADE = re.compile(r"\[selftest\]\s+(\S+?):\s+import/env failure -- degrading to Tier-0")
PASS = re.compile(r"\[selftest\]\s+(\S+?):\s+self-test PASS")
FAILED = re.compile(r"\[selftest\]\s+(\S+?):\s+self-test FAILED")
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
    ghost_rejects, repairs = [], []
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
