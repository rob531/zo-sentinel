#!/usr/bin/env python3
"""pipeline_liveness_guard.py -- outcome-based liveness alarm on the build pipeline.

WHY THIS EXISTS  (GH issue #3415, prevention 3; ledger FU-349)
  2026-08-13..08-16 the build pipeline was down for three days (~55 directives/
  day to 0) and nothing alarmed, because every existing check verified an action
  was TAKEN (process restarted, heartbeat written) rather than that an outcome
  was ACHIEVED (directives completing). A promoter can heartbeat `alive` forever
  on an in-memory module while every restart of it dies at import.

  This guard asks the outcome question directly: is there live pending work, and
  has ANY directive completed recently? It is outcome-based by construction --
  it would have caught the original outage the first morning, regardless of the
  mechanism (package-marker mutation, ghost .done graveyard, keyless ladder,
  a mechanism nobody has thought of yet).

WHAT IT DOES  (read-only; idempotent; safe to run every watchdog tick)
  1. Count LIVE pending directives: directives/pending/*.json, excluding
     terminal/audit suffixes (.done.json, .failed.json, .rejected, .duplicate,
     .expanded, .revived). The basis matters: a raw directory listing is not a
     queue depth (FU-169, FU-349).
  2. Find the newest <id>.done.json sentinel at the directives ROOT -- the one
     place goose_runner stamps completions. NOT the done/ directory, which is
     not where completions land and reads ~2/day while the true rate is ~85/day
     (FU-349 measured both instruments off by two orders of magnitude in
     opposite directions).
  3. ALARM (rc=1) iff live pending > 0 AND no .done.json was written within
     --threshold-hours (default 2). Additionally latch/clear a sentinel file
     (PIPELINE_STALLED) so other lanes can read the verdict without re-deriving
     it. Report-loud, gate nothing: this guard never blocks or restarts
     anything (R7 -- recovery over restriction is the janitor's job; this
     guard's job is to make a stall impossible to miss).

EXIT CODES
  0  healthy: pending empty (honest zero over an empty queue is not a stall),
     or a completion landed within the threshold
  1  ALARM: live pending work exists and nothing has completed in threshold
  2  cannot-evaluate: directives tree absent/unreadable. Never read 2 as
     healthy -- unknown is not zero (R6).

WHAT WOULD SHOW THIS GUARD IS WRONG
  A fixture with live pending work and only a stale .done.json must ALARM; the
  same fixture with a fresh .done.json, or with an empty pending/, must stay
  silent; a missing directives tree must exit 2. All are exercised by
  --selftest, both directions, so a green from this guard is a green that has
  been seen able to go red.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOG = os.environ.get("PLG_LOG", "/home/workspace/logs/pipeline_liveness_guard.log")
STALL_SENTINEL = os.environ.get(
    "PLG_STALL_SENTINEL", "/home/workspace/logs/PIPELINE_STALLED")

# Suffixes that make a file in pending/ NOT a live directive. Terminal
# sentinels normally live at the directives root, but the exclusion is applied
# here too so a misplaced sentinel can never inflate the queue depth (FU-169).
_NON_LIVE_SUFFIXES = (
    ".done.json", ".failed.json", ".rejected", ".duplicate", ".expanded",
    ".revived",
)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(payload: dict) -> None:
    payload["ts"] = _now()
    line = json.dumps(payload, sort_keys=True)
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _live_pending(pending_dir: str) -> int:
    n = 0
    for name in os.listdir(pending_dir):
        if not name.endswith(".json"):
            continue
        if any(name.endswith(sfx) for sfx in _NON_LIVE_SUFFIXES):
            continue
        n += 1
    return n


def _newest_mtime(root: str, suffix: str) -> float | None:
    newest = None
    for name in os.listdir(root):
        if not name.endswith(suffix):
            continue
        try:
            m = os.path.getmtime(os.path.join(root, name))
        except OSError:
            continue
        if newest is None or m > newest:
            newest = m
    return newest


def _latch(payload: dict) -> None:
    """Write the alarm sentinel. Idempotent: overwrites with current basis."""
    try:
        with open(STALL_SENTINEL, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, sort_keys=True)
    except OSError:
        pass


def _clear_latch() -> None:
    try:
        os.remove(STALL_SENTINEL)
    except FileNotFoundError:
        pass
    except OSError:
        pass


def check(directives_root: str, threshold_hours: float) -> int:
    pending_dir = os.path.join(directives_root, "pending")
    if not os.path.isdir(directives_root) or not os.path.isdir(pending_dir):
        _log({"event": "cannot_evaluate",
              "reason": "directives tree absent",
              "directives_root": directives_root})
        return 2

    try:
        pending = _live_pending(pending_dir)
        done_m = _newest_mtime(directives_root, ".done.json")
        failed_m = _newest_mtime(directives_root, ".failed.json")
    except OSError as exc:
        _log({"event": "cannot_evaluate", "reason": str(exc)})
        return 2

    now = time.time()
    done_age_h = None if done_m is None else round((now - done_m) / 3600.0, 2)
    failed_age_h = None if failed_m is None else round((now - failed_m) / 3600.0, 2)
    basis = {
        "directives_root": directives_root,
        "live_pending": pending,
        "newest_done_age_h": done_age_h,        # None = no .done.json at all
        "newest_failed_age_h": failed_age_h,    # context only, not the verdict
        "threshold_h": threshold_hours,
        "basis": "live pending = pending/*.json minus terminal/audit suffixes; "
                 "completions = *.done.json at directives ROOT (never done/)",
    }

    if pending == 0:
        _log({"event": "ok", "reason": "no live pending work", **basis})
        _clear_latch()
        return 0

    if done_age_h is not None and done_age_h <= threshold_hours:
        _log({"event": "ok", "reason": "completions flowing", **basis})
        _clear_latch()
        return 0

    alarm = {"event": "PIPELINE_STALLED",
             "reason": "live pending work and no .done.json within threshold",
             **basis}
    _log(alarm)
    _latch(alarm)
    return 1


def selftest() -> int:
    """Two-point control, both directions, in a throwaway tree."""
    import tempfile

    global STALL_SENTINEL
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        STALL_SENTINEL = os.path.join(tmp, "PIPELINE_STALLED")
        root = os.path.join(tmp, "directives")
        os.makedirs(os.path.join(root, "pending"))

        def touch(path: str, age_s: float = 0.0) -> None:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{}")
            if age_s:
                t = time.time() - age_s
                os.utime(path, (t, t))

        # 1. empty pending -> healthy, regardless of sentinel age
        rc = check(root, 2.0)
        if rc != 0:
            failures.append("empty pending must be healthy, got rc=%d" % rc)

        # 2. live pending + stale done -> ALARM (the direction that must fire)
        touch(os.path.join(root, "pending", "d1.json"))
        touch(os.path.join(root, "old.done.json"), age_s=3 * 3600)
        rc = check(root, 2.0)
        if rc != 1:
            failures.append("stale completions must ALARM, got rc=%d" % rc)
        if not os.path.exists(STALL_SENTINEL):
            failures.append("ALARM must latch the sentinel file")

        # 3. live pending + fresh done -> healthy, and the latch clears
        touch(os.path.join(root, "fresh.done.json"))
        rc = check(root, 2.0)
        if rc != 0:
            failures.append("fresh completions must be healthy, got rc=%d" % rc)
        if os.path.exists(STALL_SENTINEL):
            failures.append("healthy run must clear the sentinel file")

        # 4. live pending + NO done sentinel at all -> ALARM (None is not fresh)
        os.remove(os.path.join(root, "fresh.done.json"))
        os.remove(os.path.join(root, "old.done.json"))
        rc = check(root, 2.0)
        if rc != 1:
            failures.append("no sentinel at all must ALARM, got rc=%d" % rc)

        # 5. missing tree -> cannot-evaluate, never healthy
        rc = check(os.path.join(tmp, "nope"), 2.0)
        if rc != 2:
            failures.append("missing tree must be rc=2, got rc=%d" % rc)

    for f in failures:
        print("SELFTEST FAIL: %s" % f, file=sys.stderr)
    print("selftest: %s" % ("PASS" if not failures else "FAIL"), flush=True)
    return 0 if not failures else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--directives", default=os.path.join(REPO, "directives"))
    ap.add_argument("--threshold-hours", type=float, default=2.0)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    return check(args.directives, args.threshold_hours)


if __name__ == "__main__":
    sys.exit(main())
