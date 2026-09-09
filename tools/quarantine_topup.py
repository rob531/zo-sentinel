#!/usr/bin/env python3
"""quarantine_topup.py -- the seam that makes requeue_quarantined.py CONSULTED.

WHY THIS EXISTS (improvement-loop cycle-0087, 2026-09-09).
`tools/requeue_quarantined.py` was built to close GitHub issue #4079 (re-emit
the 69 modules #4070 quarantined). It is complete, tested in CI
(tests/test_requeue_quarantined.py is in evaluator.yml's allowlist) and was
measured DARK: 20616 bytes with NO invocation-shaped caller in 5403 repo files
+ 37 lane prompts. A tool that only a test calls has never once run against the
world. The pacing it implements in code was itself paced by prose -- its
docstring asks a "cron, a lane, or a human hitting up-arrow" to run it, and for
14 days none of the three did. THIS module is that caller, so the re-emission
proceeds without anyone remembering to start it.

WHY IT SPAWNS DETACHED RATHER THAN BLOCKING. requeue_quarantined.reference_counts
reads every .py in the repo to decide eligibility from evidence; measured on the
tower it runs in MINUTES, not seconds. goose_runner calls this from its directive
poll loop, so a blocking call would stall the builder for the duration of every
top-up. We spawn and return immediately. The emitted directives land in
pending/ and the NEXT poll picks them up -- one cycle of latency against a
builder that polls continuously is free, and a stalled builder is not.

WHY THAT IS SAFE WITHOUT A LOCK. requeue_quarantined names every directive for
its STABLE id and treats a name collision as a REFUSAL, never an overwrite, and
resolves "already handled" against live state (file back on main / directive
exists) rather than a ledger. Two overlapping runs therefore cannot double-emit;
the loser prints "SKIP (raced)". The caps (MAX_PER_RUN=5, MAX_IN_FLIGHT=6) are
enforced inside that tool and this module may only LOWER them via --limit.

WHY THE STAMP FILE IS NOT A SECOND COPY OF THE TRUTH. It records only WHEN we
last spawned, never WHAT was emitted -- that question is answered by observing
the directives tree. If the stamp is missing, unreadable or garbage we top up
anyway (fail-open): the worst case is an extra idempotent, self-capping run.

KILL SWITCH: ZO_QUARANTINE_TOPUP=0 disables the seam without a deploy.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "requeue_quarantined.py"

# Only top up when the builder's queue is THIN. The charter clause is
# "builder directives must NEVER be empty"; this is that clause in code.
# Above the floor the builder already has work and a re-emission would only
# lengthen a queue nobody is draining.
QUEUE_FLOOR = 4

# One spawn per hour at most. The bound that actually matters is the tool's own
# MAX_IN_FLIGHT; this only stops the poll loop from launching a minutes-long
# process every few seconds.
MIN_INTERVAL_S = 3600

# Per-run cap. Lower than the tool's MAX_PER_RUN=5 on purpose: this path is
# unattended, so it drains slowly enough that a bad re-emission is noticed
# before six more follow it.
LIMIT = 2

STAMP_NAME = ".quarantine_topup.stamp"


def _enabled(env=None) -> bool:
    env = os.environ if env is None else env
    return str(env.get("ZO_QUARANTINE_TOPUP", "1")).strip().lower() not in (
        "0", "false", "no", "off")


def _pending_count(pending_dir: Path) -> int:
    try:
        return sum(1 for p in Path(pending_dir).glob("*.json")
                   if not p.name.startswith((".bak", ".duplicate")))
    except Exception:                                           # noqa: BLE001
        return 0


def _stamp_age_s(stamp: Path, now: float) -> float:
    """Seconds since the last spawn. Unreadable/absent stamp => infinite age,
    i.e. fail OPEN. An unknown age is not a fresh age (R6: unknown is not zero,
    and here the safe reading of unknown is 'due')."""
    try:
        return now - float(Path(stamp).read_text(encoding="utf-8").strip())
    except Exception:                                           # noqa: BLE001
        return float("inf")


def should_topup(pending_dir, stamp_path=None, now=None, env=None):
    """(bool, reason). Pure: reads the world, changes nothing."""
    now = time.time() if now is None else now
    pending_dir = Path(pending_dir)
    stamp = Path(stamp_path) if stamp_path else pending_dir.parent / STAMP_NAME
    if not _enabled(env):
        return False, "disabled by ZO_QUARANTINE_TOPUP"
    if not TOOL.exists():
        return False, "requeue_quarantined.py not present at %s" % TOOL
    n = _pending_count(pending_dir)
    if n >= QUEUE_FLOOR:
        return False, "queue has %d pending (floor %d)" % (n, QUEUE_FLOOR)
    age = _stamp_age_s(stamp, now)
    if age < MIN_INTERVAL_S:
        return False, "throttled: last top-up %.0fs ago (interval %d)" % (
            age, MIN_INTERVAL_S)
    return True, "queue thin (%d < %d), last top-up %s" % (
        n, QUEUE_FLOOR, "never" if age == float("inf") else "%.0fs ago" % age)


def topup(pending_dir, stamp_path=None, now=None, env=None, spawn=None,
          limit: int = LIMIT):
    """Spawn a paced re-emission if the world calls for one.

    Returns (spawned: bool, reason: str). NEVER raises -- this runs inside the
    builder's poll loop and a top-up that can break the builder is worse than
    no top-up at all.
    """
    try:
        ok, reason = should_topup(pending_dir, stamp_path, now, env)
        if not ok:
            return False, reason
        stamp = (Path(stamp_path) if stamp_path
                 else Path(pending_dir).parent / STAMP_NAME)
        # Stamp BEFORE spawning: if the spawn fails we still wait out the
        # interval rather than retrying a broken command every poll.
        try:
            stamp.write_text(str(time.time() if now is None else now),
                             encoding="utf-8")
        except Exception:                                       # noqa: BLE001
            pass
        cmd = [sys.executable, str(TOOL), "--emit", "--limit", str(limit),
               "--json"]
        runner = spawn if spawn is not None else _spawn_detached
        runner(cmd)
        return True, "spawned: %s (%s)" % (" ".join(cmd), reason)
    except Exception as exc:                                    # noqa: BLE001
        return False, "top-up failed soft (%s: %s)" % (type(exc).__name__, exc)


def _spawn_detached(cmd):
    """Fire and forget. stdout/stderr to the tool's own log, never to a pipe --
    a pipe nobody drains is how a detached child blocks on a full buffer."""
    logdir = Path(os.environ.get("ZO_LOG_DIR", "/home/workspace/zo_sentinel/logs"))
    try:
        logdir.mkdir(parents=True, exist_ok=True)
        out = open(logdir / "quarantine_topup.log", "ab")
    except Exception:                                           # noqa: BLE001
        out = subprocess.DEVNULL
    kwargs = {"stdout": out, "stderr": subprocess.STDOUT, "cwd": str(REPO)}
    if os.name == "posix":
        kwargs["start_new_session"] = True
    subprocess.Popen(cmd, **kwargs)                             # noqa: S603


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pending-dir",
                    default="/home/workspace/zo_sentinel/directives/pending")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the decision without spawning")
    a = ap.parse_args(argv)
    if a.dry_run:
        ok, why = should_topup(a.pending_dir)
        print("%s: %s" % ("WOULD TOP UP" if ok else "no", why))
        return 0
    ok, why = topup(a.pending_dir)
    print("%s: %s" % ("spawned" if ok else "no", why))
    return 0


if __name__ == "__main__":
    sys.exit(main())
