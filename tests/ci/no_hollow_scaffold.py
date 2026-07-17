#!/usr/bin/env python3
"""Anti-hollow-scaffold CI gate: fail a PR that ADDS a hollow root-level module.

The LAST of the three seams that enforce this rule (builder -> publisher -> CI)
and the only one that sees the real PR diff. The rule itself is defined once, in
zo_sentinel.gates.hollow -- this file only decides WHAT to feed it (the added
files of the diff). See that module for why the scope is root-level .py only.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))   # repo root, so the package imports in CI

from zo_sentinel.gates.hollow import hollow_scaffold_scan   # noqa: E402

BASE = os.environ.get("BASE_REF", "origin/main")


def added_files():
    r = subprocess.run(["git", "diff", "--name-only", "--diff-filter=A", f"{BASE}...HEAD"],
                       capture_output=True, text=True)
    return [f.strip() for f in r.stdout.splitlines() if f.strip()]


def main():
    bad = []
    for f in added_files():
        if not f.endswith(".py"):
            continue    # gate scope is .py modules; binary assets crash a utf-8 read
        try:
            src = open(f, encoding="utf-8").read()
        except (OSError, UnicodeDecodeError):
            continue
        why = hollow_scaffold_scan(f, src)
        if why:
            bad.append((f, why))
    if bad:
        print("HOLLOW SCAFFOLD(S) DETECTED -- added modules that are not real:")
        for f, why in bad:
            print(f"  FAIL  {f}  -- {why}")
        print("\nReal modules import the app data layer (app.db/app.models) and use no mock DB.")
        return 1
    print("OK: no hollow scaffolds among added root-level modules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
