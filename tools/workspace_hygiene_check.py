#!/usr/bin/env python3
"""workspace_hygiene_check.py -- assert the build workspace still IS `main`.

Why this exists (MERGE_AUDIT_2026-08-23 B2)
-------------------------------------------
The 2026-08-14..16 build stall was not a merged defect. It was an untracked
`app/routers/__init__.py` sitting in the host build workspace
(/home/workspace/zo_sentinel), shadowing the committed PEP-420 namespace
package and eagerly importing ~55 `app.router_*` modules of which two existed.
It broke `app.routers.media_assets`, and with it the `media_assets` spine mount
and every local `import app.*`.

No gate caught it because no gate LOOKED. The build workspace is a mutable tree
that CI never observes: CI tests `origin/main`, the builder and its self-tests
run here. When the two diverge, every local gate result is measuring a tree that
does not exist anywhere else -- and reports green about it.

So the finding is not the two files. The finding is that the tree drifts
unobserved. This asserts the two properties that were false on 2026-08-14:

  1. NOT DIVERGED -- HEAD is exactly origin/main (not ahead, not behind).
     The workspace was 26 commits behind when the audit found it.
  2. NO UNTRACKED FILES UNDER app/ -- an untracked file under a package that
     `import app.*` resolves can shadow the committed one. Every one of the
     100 unresolved import sites the audit measured locally was in an
     untracked file; the same export from origin/main had zero.

Exit codes:  0 clean | 1 hygiene violation | 2 cannot evaluate (not a git repo)

Usage:
    python3 tools/workspace_hygiene_check.py            # fetches, then asserts
    python3 tools/workspace_hygiene_check.py --no-fetch # offline, cached ref
    python3 tools/workspace_hygiene_check.py --quiet    # one line unless failing
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

# Untracked paths under app/ that are build residue rather than shadowing risk.
# Kept deliberately tiny: anything that can be imported must NOT be excused.
BENIGN_SUFFIXES = (".log", ".tmp")


def git(args, cwd):
    """Run a git command; return (exit_code, stdout). Never raises."""
    p = subprocess.run(["git"] + args, cwd=cwd, capture_output=True, text=True)
    return p.returncode, p.stdout.strip()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workspace", nargs="?", default=None,
                    help="build workspace path (default: this file's repo root)")
    ap.add_argument("--no-fetch", action="store_true",
                    help="skip 'git fetch'; compare against the cached origin/main ref")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    ws = args.workspace or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    rc, top = git(["rev-parse", "--show-toplevel"], ws)
    if rc != 0:
        print("workspace-hygiene: CANNOT EVALUATE -- %s is not a git repo" % ws)
        return 2
    ws = top

    problems = []
    notes = []

    # --- fetch so "behind" is even observable -------------------------------
    # A stale remote-tracking ref makes divergence invisible, which is the exact
    # failure mode being checked for. A fetch failure is reported, never
    # silently tolerated -- but it does not stop the local assertions below.
    if not args.no_fetch:
        rc, _ = git(["fetch", "--quiet", "origin", "main"], ws)
        if rc != 0:
            notes.append("fetch FAILED -- divergence measured against a possibly stale cached ref")

    # --- 1. divergence from origin/main -------------------------------------
    rc, counts = git(["rev-list", "--left-right", "--count", "HEAD...origin/main"], ws)
    if rc != 0:
        print("workspace-hygiene: CANNOT EVALUATE -- no origin/main ref in %s" % ws)
        return 2
    try:
        ahead, behind = (int(x) for x in counts.split())
    except ValueError:
        print("workspace-hygiene: CANNOT EVALUATE -- unparseable rev-list output")
        return 2

    if behind:
        problems.append("DIVERGED: %d commit(s) BEHIND origin/main "
                        "(local gate results describe a tree that is not main)" % behind)
    if ahead:
        problems.append("DIVERGED: %d commit(s) AHEAD of origin/main "
                        "(unpushed local commits in the build workspace)" % ahead)

    # --- 2. untracked files under app/ --------------------------------------
    # --untracked-files=all so a whole untracked directory is enumerated file by
    # file rather than collapsed to one entry. .gitignore is still honoured, so
    # __pycache__ and friends do not show up here.
    rc, out = git(["status", "--porcelain", "--untracked-files=all", "--", "app/"], ws)
    untracked = [ln[3:] for ln in out.splitlines() if ln.startswith("??")]
    untracked = [p for p in untracked if not p.endswith(BENIGN_SUFFIXES)]
    if untracked:
        problems.append("UNTRACKED under app/: %d file(s) -- an untracked module can "
                        "shadow the committed package (B2)" % len(untracked))

    # --- report -------------------------------------------------------------
    if not problems:
        if not args.quiet:
            print("workspace-hygiene: OK -- %s is exactly origin/main, app/ has no untracked files" % ws)
        else:
            print("workspace-hygiene: OK")
        for n in notes:
            print("  note: %s" % n)
        return 0

    if args.quiet:
        # One line: this is what lands in the watchdog log every tick.
        print("workspace-hygiene: FAIL -- %s -- %s" % (ws, "; ".join(problems)))
        for n in notes:
            print("  note: %s" % n)
        return 1

    print("workspace-hygiene: FAIL -- %s" % ws)
    for p in problems:
        print("  * %s" % p)
    for p in untracked[:20]:
        print("      ?? %s" % p)
    if len(untracked) > 20:
        print("      ... and %d more" % (len(untracked) - 20))
    for n in notes:
        print("  note: %s" % n)
    print("  fix: commit or remove the untracked files, then "
          "`git merge --ff-only origin/main`")
    return 1


if __name__ == "__main__":
    sys.exit(main())
