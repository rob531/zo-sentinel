#!/usr/bin/env python3
"""Who still reads an ENQUEUE receipt as a stored row?  Resolved from the LIVE roster.

`POST /write` on the zo bus answers {"ok": true, "queued": 1} before the row reaches the
store.  A caller that counts the 2xx publishes rows it never stored.  Measured on
/home/workspace/logs/write_service.log for 2026-10-04 -> 2026-10-05 (23.6h), 8284 rows a
day were acknowledged and discarded across three mechanisms (absent table, absent PK for
an upsert, NOT NULL violation).

This prints the split: live modules that route writes through `bus_write_guard` versus
live modules that still post directly.  It is a census, not a gate -- it exits 0 whatever
it finds unless --require-zero is passed.

R1: the population is the `ps` roster, never a repo walk.  A repo walk on this tree finds
313 files, most of them one-off builder output that no process executes; quoting that
number as the backlog is what cycle-0164, -0176 and -0179 each paid to re-derive.
R6: a roster that cannot be read exits 2 UNKNOWN -- never 0 over an empty census.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

WRITES = re.compile(r"""["']table["']\s*:|/write["']|\bws_write\s*\(""")
GUARDED = re.compile(r"bus_write_guard|guarded_write")


def live_roster():
    """Absolute paths of .py files owned by a running process, or None if unreadable."""
    try:
        out = subprocess.run(
            ["ps", "-eo", "args"], capture_output=True, text=True, timeout=30
        )
    except Exception:
        return None
    if out.returncode != 0:
        return None
    paths = set()
    for m in re.finditer(r"(/[\w./\-]+\.py)\b", out.stdout):
        p = Path(m.group(1))
        if p.is_file():
            paths.add(p)
    return sorted(paths) or None


def classify(paths):
    guarded, unguarded = [], []
    for p in paths:
        try:
            text = p.read_text(errors="replace")
        except Exception:
            continue
        if not WRITES.search(text):
            continue
        (guarded if GUARDED.search(text) else unguarded).append(p)
    return guarded, unguarded


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--roster-from", help="file of newline-separated paths, instead of ps")
    ap.add_argument("--require-zero", action="store_true",
                    help="exit 1 while any live writer is still unguarded")
    args = ap.parse_args(argv)

    if args.roster_from:
        paths = [Path(l.strip()) for l in Path(args.roster_from).read_text().splitlines() if l.strip()]
        basis = f"roster file {args.roster_from}"
    else:
        paths = live_roster()
        basis = "ps -eo args (live process roster)"

    if not paths:
        print("UNKNOWN, not zero: the process roster could not be read. basis:", basis)
        return 2

    guarded, unguarded = classify(paths)
    total = len(guarded) + len(unguarded)
    print(f"basis: {basis}; {len(paths)} live module file(s), {total} of them write to the bus")
    print(f"GUARDED   {len(guarded):3d}")
    for p in guarded:
        print(f"    + {p}")
    print(f"UNGUARDED {len(unguarded):3d}  (a 2xx from /write is still counted as a row)")
    for p in unguarded:
        print(f"    - {p}")
    if args.require_zero and unguarded:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
