#!/usr/bin/env python3
"""S7 -- stop regeneration: write the builder's family exclusion input from the ledger.

`directives/builder_exclusions.json` lists every family the builder must not rebuild:
promoted and superseded staged families (from the ledger) and every family already
live under services/active. The readers are in zo_sentinel/builder_exclusions.py
(the proposal fan-out and the architect's starvation floor). This file is derived;
do not hand-edit it -- the next tick regenerates it.

    python tools/staging_drain/exclusions.py            # ledger -> directives/builder_exclusions.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
from zo_sentinel.builder_exclusions import DEFAULT_PATH, family_of  # noqa: E402

DEFAULT_LEDGER = os.path.join(ROOT, "chairman", "staging_drain", "ledger.json")
ACTIVE = os.path.join(ROOT, "services", "active")


def build(ledger, active_dir=ACTIVE):
    fams = {}
    if os.path.isdir(active_dir):
        for n in sorted(os.listdir(active_dir)):
            if n.startswith((".", "_")) or not os.path.isdir(os.path.join(active_dir, n)):
                continue
            fams[family_of(n)] = {"reason": "active: services/active/%s is live" % n, "since": None}
    for name, row in sorted(ledger.get("services", {}).items()):
        if row["outcome"] == "promoted":
            fams[row["family"]] = {"reason": "promoted: %s (%s)" % (name, row.get("reason", "")), "since": row.get("promoted_at")}
        elif row["outcome"] == "superseded":
            by = row.get("superseded_by", "")
            # a family superseded by ITSELF (an older version losing to a newer one) is
            # not excluded -- the newer version is still being drained
            if by.startswith("active:") or family_of(by) != row["family"]:
                fams.setdefault(row["family"], {"reason": "superseded: %s by %s" % (name, by), "since": None})
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "writer": "tools/staging_drain/exclusions.py",
        "basis": {"ledger_generated_at": ledger.get("generated_at")},
        "families": fams,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--out", default=DEFAULT_PATH)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)
    if not os.path.isfile(args.ledger):
        print("UNKNOWN: no ledger at %s" % args.ledger)
        return 2
    with open(args.ledger, encoding="utf-8") as fh:
        ledger = json.load(fh)
    data = build(ledger)
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1)
    if not args.quiet:
        print("=== staging_drain S7 exclusions ===\n  families excluded: %d\n  written: %s" % (len(data["families"]), args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
