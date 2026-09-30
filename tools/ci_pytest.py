#!/usr/bin/env python3
"""Resolve the evaluator's pytest target set BY CONSTRUCTION, not from a hand list.

WHY THIS EXISTS
---------------
`.github/workflows/evaluator.yml` carried a hand-maintained list of 74
`tests/test_*.py` filenames while 118 more sat on disk, uncollected. The
`pytest` context is a REQUIRED check, so those 118 files gated nothing: a lane
could add a test, watch the check go green, and publish that green as evidence
for a claim its own test had never been asked.

That is not hypothetical. On 2026-09-30 (cycle-0160) the census read
192 files on disk / 71 collected / 121 never run, and three consecutive
improvement-loop cycles -- 0158 (FU-568), 0159 (FU-570), 0160 (FU-572) -- each
shipped a negative-control suite this check never executed. The 2026-09-01 note
in evaluator.yml records the same class, and the cure applied then was *adding
six filenames by hand*, which is why it recurred within the month.

A list a human must remember to extend is the prose remedy of rule 1 wearing a
YAML hat. So the list is gone. The target set is now:

    every tests/test_*.py on disk   MINUS   tests/ci/pytest_quarantine.txt

Adding a test file is now sufficient to have it run. Excluding one requires
naming it, with a reason, in a tracked file that
`tests/test_c162_ci_target_parity.py` audits on every run of this very check.

EXIT CODES -- no outcome collapsed into another
-----------------------------------------------
    0   pytest ran and every collected test passed
    1   pytest ran and something FAILED (or collection errored)
    2   REFUSED / NEVER RAN. An empty target set is *this*, never 0:
        "no tests collected" is not a pass (R4). A missing quarantine file is
        also this -- unknown is not zero (R6).

MODES
-----
    python tools/ci_pytest.py -q --junitxml=artifacts/junit.xml
        compute the targets and run pytest with them; unrecognised argv is
        passed straight through to pytest.

    python tools/ci_pytest.py --print
        print the resolved target set, one per line, and exit 0. Runs nothing.

    python tools/ci_pytest.py --census
        print the three populations and their sizes as JSON. Runs nothing.

    python tools/ci_pytest.py --triage
        run each QUARANTINED file on its own and report which ones now pass.
        This is the mechanical way to empty the quarantine: a later lane reads
        the PASSES block, deletes those lines from the quarantine file, and the
        constructor picks them up with no workflow edit at all. rc is 0 when
        the triage itself completed -- it is a report, not a gate.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
QUARANTINE = os.path.join(ROOT, "tests", "ci", "pytest_quarantine.txt")
TEST_GLOB = "tests/test_*.py"


def _norm(p):
    return p.replace("\\", "/").strip()


def on_disk(root=ROOT):
    """Every top-level tests/test_*.py, repo-relative, sorted."""
    return sorted(_norm(p) for p in glob.glob(TEST_GLOB, root_dir=root))


def read_quarantine(path=QUARANTINE):
    """Return (paths, {path: reason}).

    Format, one entry per line:  tests/test_foo.py  # reason

    A line with no reason parses (so a stale file can still be read) but is
    rejected by tests/test_c162_ci_target_parity.py -- an exclusion with no
    stated reason is how a quarantine becomes a graveyard.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    paths = []
    reasons = {}
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            p, _, why = line.partition("#")
            p = _norm(p)
            if not p:
                continue
            paths.append(p)
            reasons[p] = why.strip()
    return paths, reasons


def resolve(root=ROOT, quarantine_path=QUARANTINE):
    disk = on_disk(root)
    q, reasons = read_quarantine(quarantine_path)
    qset = set(q)
    dset = set(disk)
    return {
        "disk": disk,
        "quarantined": q,
        "reasons": reasons,
        "targets": [p for p in disk if p not in qset],
        "stale": [p for p in q if p not in dset],
    }


def _triage(r):
    """Run each quarantined file alone. Report, do not gate."""
    passes, fails, missing = [], [], []
    for p in r["quarantined"]:
        if not os.path.exists(os.path.join(ROOT, p)):
            missing.append(p)
            continue
        rc = subprocess.call(
            [sys.executable, "-m", "pytest", p, "-q", "-p", "no:cacheprovider"],
            cwd=ROOT,
        )
        (passes if rc == 0 else fails).append((p, rc))
    print("\n================ TRIAGE ================")
    print("PASSES -- delete these lines from tests/ci/pytest_quarantine.txt")
    print("         and they run with NO workflow edit at all:")
    for p, _ in passes:
        print("  %s" % p)
    print("STILL RED (%d):" % len(fails))
    for p, rc in fails:
        print("  %s  rc=%d" % (p, rc))
    if missing:
        print("STALE ENTRIES (no such file) (%d):" % len(missing))
        for p in missing:
            print("  %s" % p)
    print(
        "basis: %d quarantined, %d pass, %d red, %d stale"
        % (len(r["quarantined"]), len(passes), len(fails), len(missing))
    )
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--print", dest="do_print", action="store_true")
    ap.add_argument("--census", action="store_true")
    ap.add_argument("--triage", action="store_true")
    ap.add_argument("--quarantine", default=QUARANTINE)
    args, passthrough = ap.parse_known_args(argv)

    try:
        r = resolve(ROOT, args.quarantine)
    except FileNotFoundError as exc:
        # R6: a missing exclusion file is UNKNOWN, not "nothing excluded".
        print("ci_pytest: REFUSED -- quarantine file not found: %s" % exc, file=sys.stderr)
        return 2

    if r["stale"]:
        # Loud in every mode: a quarantined path that no longer exists means a
        # test was renamed or deleted and the exclusion silently widened.
        print(
            "ci_pytest: WARNING -- %d STALE quarantine entry(ies), no such file: %s"
            % (len(r["stale"]), ", ".join(r["stale"])),
            file=sys.stderr,
        )

    if args.census:
        print(
            json.dumps(
                {
                    "on_disk": len(r["disk"]),
                    "quarantined": len(r["quarantined"]),
                    "targets": len(r["targets"]),
                    "stale": r["stale"],
                },
                indent=2,
            )
        )
        return 0

    if args.do_print:
        for p in r["targets"]:
            print(p)
        return 0

    if args.triage:
        return _triage(r)

    if not r["targets"]:
        # R4/R3: no tests collected is NOT a pass, and must never exit 0.
        print(
            "ci_pytest: REFUSED -- the target set is EMPTY (%d on disk, %d quarantined). "
            "A run that collects nothing is not a green."
            % (len(r["disk"]), len(r["quarantined"])),
            file=sys.stderr,
        )
        return 2

    print(
        "ci_pytest: %d target(s) = %d on disk - %d quarantined"
        % (len(r["targets"]), len(r["disk"]), len(r["quarantined"])),
        flush=True,
    )
    rc = subprocess.call(
        [sys.executable, "-m", "pytest"] + r["targets"] + list(passthrough), cwd=ROOT
    )
    # pytest's 5 == "no tests collected"; never let that reach the caller as 0.
    if rc == 5:
        print("ci_pytest: pytest collected NOTHING -- reporting 2, not 0", file=sys.stderr)
        return 2
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
