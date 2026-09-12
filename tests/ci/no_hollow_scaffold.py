#!/usr/bin/env python3
"""Anti-hollow CI gate: fail a PR that ADDS a hollow module OR a hollow service member.

The LAST of the seams that enforce this rule (builder -> publisher -> CI) and the only
one that sees the real PR diff. The rules themselves are defined once, in
zo_sentinel.gates.hollow -- this file only decides WHAT to feed them (the added files of
the diff).

WHY THE SECOND RULE WAS ADDED, 2026-08-08 (daily-chairman-review):
  This job is named `no-hollow` and is a REQUIRED status check on main. It called
  exactly one predicate, `hollow_scaffold_scan`, whose scope is ROOT-LEVEL .py only. The
  sibling predicate `hollow_service_member_scan` -- the one that covers
  services/{staged,active}/*/(contract|logic|router).py -- was armed at the builder, the
  publisher and the promoter seam, and was NEVER armed here.

  So a required check called `no-hollow` was green on every hollow service member ever
  opened, by construction. Measured, not inferred: PR #2757 added
  services/staged/verdict_health/contract.py, whose ENTIRE content is the 33 bytes
  `# Let me check the exemplar first` -- a model's deliberation written to the output
  path. Every one of the 7 required checks passed. It merged. The same live predicate
  this file now imports rejects that exact blob with "zero top-level statements".

  This is the fleet's largest failure class in its purest form: THE CHECK THAT WAS GREEN
  IS NOT THE CHECK THAT WOULD HAVE CAUGHT IT. Two rules, one module, one name, one seam
  armed. Nobody had to be careless; the name did the deceiving.

  R7 -- RECOVERY, NOT RESTRICTION. This adds no new gate and no new rule. It feeds an
  EXISTING, negative-controlled predicate the enumeration it was always meant to see, and
  it IMPORTS it rather than re-implementing it, so the CI seam and the promoter seam can
  never drift apart. That is the #2877 discipline, applied one seam later.

  SCOPE, STATED SO NOBODY OVERCLAIMS THIS: `--diff-filter=A` means ADDED files only. The
  7 hollow members already on origin/main (measured this run, out of 397 tracked service
  members -- the predicate accepts the other 390, so it is a gate and not a wall) are NOT
  retroactively failed by this change, and no existing PR turns red because of it. The
  retrospective half is a separate decision about live files; this is the prospective
  half, which needed nobody's permission and had been free the whole time.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))   # repo root, so the package imports in CI

from zo_sentinel.gates.hollow import (  # noqa: E402
    hollow_scaffold_scan,
    hollow_service_member_scan,
)

BASE = os.environ.get("BASE_REF", "origin/main")


def added_files():
    r = subprocess.run(["git", "diff", "--name-only", "--diff-filter=A", f"{BASE}...HEAD"],
                       capture_output=True, text=True)
    return [f.strip() for f in r.stdout.splitlines() if f.strip()]


def main():
    bad = []
    scanned = 0
    for f in added_files():
        if not f.endswith(".py"):
            continue    # gate scope is .py modules; binary assets crash a utf-8 read
        try:
            src = open(f, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        scanned += 1
        # Both limbs. Each is scoped by its own path predicate inside hollow.py, so a
        # file that belongs to neither scope is simply returned None by both -- there is
        # no double-jeopardy and no widening of either rule.
        for rule, why in (("scaffold", hollow_scaffold_scan(f, src)),
                          ("service-member", hollow_service_member_scan(f, src))):
            if why:
                bad.append((f, rule, why))
    if bad:
        print("HOLLOW FILE(S) DETECTED -- added modules that are not real:")
        for f, rule, why in bad:
            print(f"  FAIL  [{rule}]  {f}  -- {why}")
        print("\nReal modules import the app data layer (app.db/app.models) and use no "
              "mock DB. Real service members carry top-level statements, and a real "
              "contract.py asserts something or has a live __main__.")
        return 1
    # R5: publish the BASIS. A green that does not say what it looked at is the shape
    # that let FU-256 dam 87 PRs on a check running zero tests -- print the COUNT.
    print(f"OK: no hollow files among {scanned} added .py file(s) "
          f"[rules: scaffold + service-member]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
