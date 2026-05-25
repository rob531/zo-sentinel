#!/usr/bin/env python3
"""
reset_breaker.py -- Human-in-loop reset for the Gate 8 circuit breaker.

When Gate 8 observes too many cohort failures, the breaker trips. In the
tripped state, the directive generator WILL NOT propose rebuilds of any
failed files. Normal new work still flows.

A trip means "stop and have a human look before automating more rebuilds."
Running this script is the humans-looked-at-it acknowledgement.

Usage:
    # See current state
    python3 reset_breaker.py status

    # Move breaker from tripped -> half-open (allows one batch)
    python3 reset_breaker.py half-open "reviewed minimax output manually"

    # Move breaker from tripped -> closed (fully back to normal)
    python3 reset_breaker.py close "signal_analyser fixed; resuming"

    # Release a specific file from quarantine (without moving it on disk --
    # that's a separate manual 'mv' step)
    python3 reset_breaker.py release signal_enrichment_aggregator.py "spec §5 now in prompt"

    # List quarantined files
    python3 reset_breaker.py list-quarantined

What this script does NOT do:
    - Does not MOVE any files on disk -- quarantined files stay in
      /home/workspace/zo_sentinel/quarantine/ until you mv them yourself
    - Does not reset the 'recent_cohorts' history (intentional -- keeps
      audit trail visible)
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/home/workspace/zo_sentinel")
import gate_quality_state as gqs  # noqa: E402


def _print_status():
    s = gqs.snapshot()
    print(f"state:              {s.get('state')}")
    print(f"state_changed_at:   {s.get('state_changed_at')}")
    print(f"state_changed_reason: {s.get('state_changed_reason')}")
    cohorts = s.get("recent_cohorts", [])
    print(f"\nrecent cohorts ({len(cohorts)}):")
    for c in cohorts[-10:]:
        print(f"  {c['observed_at']}  {c['id']:<16s}  size={c['size']:<3d}  fail={c['fail_rate']:.0%}")
    q = s.get("quarantined", {})
    print(f"\nquarantined ({len(q)}):")
    for fn, meta in q.items():
        print(f"  {fn}  (at {meta['quarantined_at']}; {meta['reason']})")
    r = s.get("file_retries", {})
    print(f"\nfiles in retry accounting ({len(r)}):")
    for fn, meta in r.items():
        print(f"  {fn}  attempts={meta['attempts']}/{gqs.MAX_REBUILDS}  last={meta.get('last_failed_at')}")


def _set_state(target: str, reason: str):
    try:
        new = gqs.set_breaker_state(target, reason)
        print(f"[OK] state is now {new['state']!r}")
        print(f"     reason: {new['state_changed_reason']}")
    except ValueError as e:
        print(f"[FAIL] {e}", file=sys.stderr)
        sys.exit(2)


def _release(filename: str, note: str):
    ok = gqs.release_quarantine(filename, note)
    if ok:
        print(f"[OK] released {filename} from quarantine")
        qpath = Path("/home/workspace/zo_sentinel/quarantine") / filename
        if qpath.exists():
            print(f"  NOTE: file still physically at {qpath}")
            print("  To reinstate, you must: mv {} /home/workspace/zo_sentinel/".format(qpath))
    else:
        print(f"[NOOP] {filename} was not in quarantine/retry state")


def _list_quarantined():
    s = gqs.snapshot()
    q = s.get("quarantined", {})
    if not q:
        print("[info] no files currently quarantined")
        return
    for fn, meta in q.items():
        print(f"{fn}")
        print(f"  quarantined_at: {meta['quarantined_at']}")
        print(f"  reason:         {meta['reason']}")
        print(f"  attempts:       {meta.get('attempts_when_quarantined')}")
        qpath = Path("/home/workspace/zo_sentinel/quarantine") / fn
        print(f"  on_disk:        {'yes' if qpath.exists() else 'MISSING'} ({qpath})")
        print()


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] == "status":
        _print_status()
        return 0
    cmd = argv[0]
    if cmd == "close":
        reason = " ".join(argv[1:]) or "no reason given"
        _set_state("closed", reason)
        return 0
    if cmd == "half-open":
        reason = " ".join(argv[1:]) or "no reason given"
        _set_state("half-open", reason)
        return 0
    if cmd == "release":
        if len(argv) < 2:
            print("usage: reset_breaker.py release <filename> [note]", file=sys.stderr)
            return 2
        filename = argv[1]
        note = " ".join(argv[2:]) or ""
        _release(filename, note)
        return 0
    if cmd == "list-quarantined":
        _list_quarantined()
        return 0
    print(f"unknown command: {cmd!r}", file=sys.stderr)
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())