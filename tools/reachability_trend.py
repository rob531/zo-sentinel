#!/usr/bin/env python3
"""reachability_trend.py -- the detector the ratchet cannot be.

WHY THIS EXISTS (CofC ruling 2026-07-21, MUST 3)
------------------------------------------------
The ratchet is a per-PR CI gate. Its census is uploaded as a CI artifact, which
means in practice it is read NEVER -- the 246 -> 276 drift over 2026-07-20/21
was caught only because a human downloaded the artifact by hand. That is the
same shape as this project's most expensive failures: an instrument that reports
faithfully into a place nobody looks (the freshness endpoint serving a healthy
timestamp over a dead import; the builder dead 61.6h across three daily reviews).
The recorded lesson is "absence has no detector."

This script is the detector. It appends ONE dated row per day to a durable CSV
on the tower and alarms on two conditions the per-PR gate structurally cannot
see, because both are about a TREND across days rather than a delta within a PR:

  1. STALLED MOUNTING -- routers_added > 0 and mounts_added == 0 for 3
     consecutive days. This is the condition that produced the graveyard: the
     factory ships routers, nothing ever mounts them, and every individual PR
     looks fine.
  2. EXEMPTION INFLATION -- exempted_count rising. Seat 3 named this the way
     the ratchet goes decorative while still reporting green: add modules to
     the exempt list under merge pressure and the gate passes forever.

No daemon, no new service, no DB. Reads the ratchet artifact, appends a row,
exits non-zero on alarm. Run it from the daily chairman review or any cron.

    python tools/reachability_trend.py --artifact artifacts/reachability_ratchet.json
    python tools/reachability_trend.py --log "D:/zo/Zocomputer Agents/reachability_trend.csv"
    python tools/reachability_trend.py --check-only     # alarm without appending
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time

FIELDS = [
    "date",
    "router_modules_total",
    "mounted_count",
    "orphan_count",
    "exempted_count",
    "deferred_active_count",
    "baseline",
    "mode",
    "routers_added",
    "mounts_added",
    "deferred_added",
]

STALL_DAYS = 3


def read_log(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, newline="", encoding="utf-8") as fh:
            return [r for r in csv.DictReader(fh) if r.get("date")]
    except OSError:
        return []


def _int(row, key):
    try:
        return int(row.get(key) or 0)
    except (TypeError, ValueError):
        return 0


def build_row(data, prev):
    """A row is a fact plus its three deltas against yesterday."""
    row = {
        "date": time.strftime("%Y-%m-%d", time.gmtime()),
        "router_modules_total": data.get("router_modules_total", 0),
        "mounted_count": data.get("mounted_count", 0),
        "orphan_count": data.get("orphan_count", 0),
        "exempted_count": data.get("exempted_count", 0),
        "deferred_active_count": data.get("deferred_active_count",
                                          len(data.get("deferred_active", []))),
        "baseline": data.get("baseline", ""),
        "mode": data.get("mode", ""),
    }
    if prev:
        row["routers_added"] = row["router_modules_total"] - _int(prev, "router_modules_total")
        row["mounts_added"] = row["mounted_count"] - _int(prev, "mounted_count")
        row["deferred_added"] = row["deferred_active_count"] - _int(prev, "deferred_active_count")
    else:
        # First row has no predecessor. Report empty rather than 0 -- a fabricated
        # zero here would read as "no drift" and start the stall counter wrong.
        row["routers_added"] = ""
        row["mounts_added"] = ""
        row["deferred_added"] = ""
    return row


def append_row(path, row):
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    exists = os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        if not exists:
            w.writeheader()
        w.writerow(row)


def alarms(rows):
    """Return a list of human-readable alarm strings. Empty list == healthy."""
    out = []
    if not rows:
        return out

    # 1. stalled mounting over the trailing window
    tail = [r for r in rows[-STALL_DAYS:] if r.get("routers_added") not in ("", None)]
    if len(tail) >= STALL_DAYS and all(
        _int(r, "routers_added") > 0 and _int(r, "mounts_added") == 0 for r in tail
    ):
        added = sum(_int(r, "routers_added") for r in tail)
        out.append(
            "STALLED MOUNTING: %d consecutive days with new routers and ZERO new "
            "mounts (+%d routers over the window, mounted_count flat at %s). This "
            "is the condition that built the graveyard."
            % (STALL_DAYS, added, tail[-1].get("mounted_count"))
        )

    # 2. exemption inflation
    if len(rows) >= 2:
        first, last = _int(rows[0], "exempted_count"), _int(rows[-1], "exempted_count")
        if last > first:
            out.append(
                "EXEMPTION INFLATION: exempted_count %d -> %d since %s. An exemption "
                "list that grows is how this gate goes decorative while reporting "
                "green -- confirm every entry carries a reason."
                % (first, last, rows[0].get("date"))
            )

    # 3. the gate silently reverted to observe
    if rows[-1].get("mode") == "observe":
        out.append(
            "MODE REGRESSION: latest run recorded mode=observe. The ratchet was "
            "armed to enforce on 2026-07-21; if this is not a deliberate, "
            "ledgered revert then the flag was dropped."
        )
    return out


def main():
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser()
    ap.add_argument("--artifact",
                    default=os.path.join(here, "artifacts", "reachability_ratchet.json"))
    ap.add_argument("--log", default=os.environ.get(
        "REACHABILITY_TREND_LOG",
        os.path.join(here, "artifacts", "reachability_trend.csv")))
    ap.add_argument("--check-only", action="store_true",
                    help="evaluate alarms without appending today's row")
    args = ap.parse_args()

    if not os.path.exists(args.artifact):
        print("NO ARTIFACT at %s -- run tools/reachability_ratchet.py first." % args.artifact)
        return 2
    try:
        data = json.load(open(args.artifact, encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print("UNREADABLE ARTIFACT: %s" % exc)
        return 2

    rows = read_log(args.log)
    today = time.strftime("%Y-%m-%d", time.gmtime())

    if not args.check_only:
        if rows and rows[-1].get("date") == today:
            print("row for %s already present -- not double-appending" % today)
        else:
            row = build_row(data, rows[-1] if rows else None)
            append_row(args.log, row)
            rows.append(row)
            print("appended %s: routers=%s mounted=%s orphans=%s deferred=%s "
                  "(+%s routers, +%s mounts)"
                  % (row["date"], row["router_modules_total"], row["mounted_count"],
                     row["orphan_count"], row["deferred_active_count"],
                     row["routers_added"], row["mounts_added"]))

    print("\n=== reachability trend (%s, %d row(s)) ===" % (args.log, len(rows)))
    for r in rows[-7:]:
        print("  %s  routers=%-4s mounted=%-4s orphans=%-4s exempt=%-3s deferred=%-3s "
              "(+%s routers, +%s mounts)"
              % (r.get("date"), r.get("router_modules_total"), r.get("mounted_count"),
                 r.get("orphan_count"), r.get("exempted_count"),
                 r.get("deferred_active_count"), r.get("routers_added"),
                 r.get("mounts_added")))

    fired = alarms(rows)
    if fired:
        print("\nALARM (%d):" % len(fired))
        for a in fired:
            print("  * %s" % a)
        return 1
    print("\nno alarm.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
