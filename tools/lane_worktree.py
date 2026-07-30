#!/usr/bin/env python3
r"""Lane-private worktrees -- so pinning a checkout stops being a shared mutation.

WHY (FU-198 follow-on, 2026-07-30)
----------------------------------
`runbook_pin.py` correctly diagnosed that `D:\zo\_runbook` is shared mutable state
and that a stale tool there ANSWERS rather than fails. Its `--heal` re-detaches the
worktree to origin/main. That repair is right for a tree you OWN and wrong for a
tree you SHARE, because it is the same act that caused the incident:

    09:39:49Z  prod-drift parks _runbook at ae71dafd to gate the candidate
    09:49:38Z  back to origin/main

A heal firing inside that window yanks the tree out from under a running prod gate
-- and because `accept_gate.py` exists at BOTH shas, the gate keeps going, silently,
against a different tree. Fixing contention by adding a second mutator makes the
race worse. Worse still as doctrine: "heal FIRST" across 13 lanes turns an
occasional collision into a scheduled one.

The reflog also shows lanes doing BRANCH WORK AND COMMITS in the shared tree
(`fix/fu-184-would-fire-enum` + 3 commits at 00:55-01:02). No pin can make that
safe. The fix is not a better lock on one tree; it is one tree per lane.

Worktrees are cheap and git already enforces the one invariant that matters: a
branch cannot be checked out in two worktrees at once. That is the same mechanism
that -- when violated by an ABANDONED worktree holding `main` since 7/06 -- kept the
primary clone 992 commits stale for 23 days. Used deliberately it is a feature.

USAGE
    python tools/lane_worktree.py --ensure prod-drift
    python tools/lane_worktree.py --list
    python tools/lane_worktree.py --check D:\zo\_runbook     # is this path shared?
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LANES_ROOT = os.environ.get("ZO_LANES_ROOT", r"D:\zo\_lanes")

# Paths KNOWN to be traversed by more than one lane. `_runbook` earned its place
# empirically: 10+ checkouts by different actors in 14 hours, including commits.
# The primary clone is listed because it is where humans look and where daemons
# resolve code -- an agent must never re-point it.
SHARED_PATHS = [
    r"D:\zo\_runbook",
    r"D:\zo\zo-sentinel\zo-sentinel",
]


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(os.path.abspath(p)))


def is_shared(path: str) -> bool:
    return _norm(path) in {_norm(p) for p in SHARED_PATHS}


def lane_slug(lane: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", lane)


def lane_path(lane: str, root: str | None = None) -> str:
    return os.path.join(root or LANES_ROOT, lane_slug(lane))


def _git(cwd: str, *args) -> tuple[int, str]:
    r = subprocess.run(["git", "-C", cwd, *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()


def ensure(lane: str, target: str = "origin/main", repo: str | None = None,
           root: str | None = None, runner=_git) -> dict:
    """Create or refresh a lane's PRIVATE worktree, detached at `target`.

    Detached on purpose: a lane that holds a NAMED branch would reintroduce exactly
    the squatting failure that froze `main` for 23 days. Idempotent -- ensuring an
    already-correct worktree is a no-op that still reports ok.
    """
    repo = repo or ROOT
    path = lane_path(lane, root)
    created = False

    if not os.path.isdir(os.path.join(path, ".git")) and not os.path.isfile(
            os.path.join(path, ".git")):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rc, out = runner(repo, "worktree", "add", "--detach", path, target)
        if rc != 0:
            return {"lane": lane, "path": path, "ok": False, "error": out[:300]}
        created = True

    runner(path, "fetch", "origin", "--quiet")
    rc, out = runner(path, "checkout", "--detach", target, "-q")
    if rc != 0:
        return {"lane": lane, "path": path, "ok": False, "created": created,
                "error": out[:300]}
    _, head = runner(path, "rev-parse", "HEAD")
    _, tgt = runner(path, "rev-parse", target)
    _, dirty = runner(path, "status", "--porcelain")
    return {
        "lane": lane, "path": path, "ok": head == tgt, "created": created,
        "head": head[:12], "target": tgt[:12],
        "dirty": len([x for x in dirty.splitlines() if x.strip()]),
        "shared": is_shared(path),
    }


def list_lanes(root: str | None = None) -> list:
    r = root or LANES_ROOT
    if not os.path.isdir(r):
        return []
    out = []
    for n in sorted(os.listdir(r)):
        p = os.path.join(r, n)
        if not os.path.isdir(p):
            continue
        _, head = _git(p, "rev-parse", "--short", "HEAD")
        _, behind = _git(p, "rev-list", "--count", "HEAD..origin/main")
        out.append({"lane": n, "path": p, "head": head, "behind": behind})
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="lane-private worktrees")
    ap.add_argument("--ensure")
    ap.add_argument("--target", default="origin/main")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--check", help="report whether a path is shared")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.check:
        shared = is_shared(a.check)
        print("%s : %s" % (a.check, "SHARED -- do not heal unattended" if shared
                          else "not a known shared path"))
        return 1 if shared else 0
    if a.ensure:
        r = ensure(a.ensure, a.target)
        print(json.dumps(r, indent=1) if a.json else
              "%s -> %s  head=%s ok=%s" % (r["lane"], r["path"], r.get("head"), r["ok"]))
        return 0 if r["ok"] else 1
    for r in list_lanes():
        print("  %-24s %-10s behind=%s" % (r["lane"], r["head"], r["behind"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
