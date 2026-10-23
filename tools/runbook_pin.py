#!/usr/bin/env python3
r"""Prove the RUNBOOK WORKTREE is at origin/main -- because a path is not a version.

WHY THIS EXISTS (FU-197, 2026-07-30)
------------------------------------
Every sentinel lane is told to run its tools "from D:\\zo\\_runbook (refreshed to
origin/main)". That sentence names a PATH. Nothing ever refreshed it, nothing ever
owned it, and any sibling lane may `git checkout --detach <anything>` into it.

At 13:39:49Z on 2026-07-30 something parked it at ae71dafd -- the *staged candidate*
sha, 131 commits behind main. Ten minutes later this task ran, and:

  * `tools/shadow_decision.py` did NOT exist at that sha (it landed in #2296), so the
    D:\\zo\\Zocomputer Agents\\_tools\\shadow_decision.py forwarder skipped its primary
    candidate and announced, out loud on stderr, that it was falling through to the
    shared checkout. That worked exactly as designed.
  * `tools/accept_gate.py` DID exist at that sha -- and differs from main
    (70525a30 vs b1477e0e). It would have run. Silently. At the old version.

That asymmetry is the whole lesson and it is inverted from the risk:

    A MISSING tool fails LOUDLY. A STALE tool runs QUIETLY AND ANSWERS.

The forwarder's fallback notice is a real guard, but it only fires for the files that
happen to be ABSENT at whatever sha the worktree got parked at, which is an accident of
commit history, not a property of danger. `accept_gate` is the verdict that decides
whether a prod fire is accepted; it is the last tool that should be resolved by
"whatever file is sitting at this path right now".

R1 says: resolve the running artifact from the runtime and be able to name HOW. Naming a
path does not do that -- `D:\zo\_runbook\tools\accept_gate.py` is the same string on the
day it is right and the day it is 131 commits stale. This tool names the COMMIT.

WHAT IT MEASURES
----------------
The worktree's HEAD vs `origin/main`, and then -- the part that matters -- for each
sentinel tool, whether the blob at HEAD differs from the blob at origin/main, split into:

    absent    : missing at HEAD, present at target  -> fails LOUD (a caller notices)
    divergent : present at BOTH and DIFFERENT       -> fails SILENT (a caller does not)

`divergent` is the headline. A drifted worktree whose divergent set is empty is still
DRIFTED -- "no divergence today" is a property of the delta, not of the discipline, and
tomorrow's delta is different.

RECOVERY OVER RESTRICTION (R7). `--heal` re-detaches the worktree to origin/main and
re-classifies, converging rather than refusing. It is idempotent: healing an
already-pinned worktree is a no-op that still returns 0.

EXIT CODES -- read the CODE, not the printed line:
    0  PINNED    HEAD == origin/main
    1  DRIFTED   HEAD != origin/main (whether or not any tool actually diverged)
    2  UNKNOWN   cannot evaluate: path missing, not a git worktree, or no origin/main ref

2 IS NOT A PASS. A worktree that cannot be inspected is not a worktree that is correct;
unknown is not zero (R6).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Callable, Dict, List, Optional, Tuple

RC_PINNED = 0
RC_DRIFTED = 1
RC_UNKNOWN = 2

DEFAULT_WORKTREE = r"D:\zo\_runbook"
DEFAULT_TARGET = "origin/main"

# The tools this project's lanes execute BY ABSOLUTE PATH out of the runbook worktree.
# If you add one, add it here -- an unlisted tool is an unmeasured tool.
SENTINEL_TOOLS = [
    "tools/accept_gate.py",
    "tools/fire_gate.py",
    "tools/rollback_anchor_probe.py",
    "tools/sentinel_run_ledger.py",
    "tools/shadow_decision.py",
]


def _is_shared(path: str) -> bool:
    """Ask lane_worktree, which owns the list. Fail SAFE: if the oracle cannot be
    imported we treat the path as shared, because the dangerous default is to heal."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from lane_worktree import is_shared
    except Exception:
        return True
    return is_shared(path)


# --------------------------------------------------------------- pure classification
def classify(
    head: Optional[str],
    target: Optional[str],
    blobs: Dict[str, Tuple[Optional[str], Optional[str]]],
    dirty: Optional[List[str]] = None,
) -> dict:
    """Classify a worktree from already-resolved facts. No I/O -- so it is testable.

    `blobs` maps tool path -> (blob-oid-at-head, blob-oid-at-target); None means the
    file does not exist at that commit.

    `dirty` lists sentinel tools with UNCOMMITTED working-tree edits. A commit oid
    describes what was COMMITTED, not what is on disk, and python executes the file on
    disk -- so a worktree sitting exactly at origin/main with an edited accept_gate.py
    is running something no commit contains. Omitting this check would have left the
    tool with the exact blind spot it exists to remove.
    """
    dirty = sorted(dirty or [])
    if not head or not target:
        return {
            "rc": RC_UNKNOWN,
            "verdict": "UNKNOWN",
            "head": head,
            "target": target,
            "absent": [],
            "divergent": [],
            "identical": [],
            "dirty": dirty,
            "reason": "could not resolve HEAD and/or target commit",
        }

    absent: List[str] = []
    divergent: List[str] = []
    identical: List[str] = []
    for path in sorted(blobs):
        at_head, at_target = blobs[path]
        if at_head is None and at_target is not None:
            absent.append(path)
        elif at_head is not None and at_target is not None and at_head != at_target:
            divergent.append(path)
        elif at_head == at_target:
            identical.append(path)
        else:
            # present at HEAD, gone at target: a tool that was REMOVED upstream. Still
            # a difference between what runs and what should -- count it as divergent
            # rather than inventing a fourth bucket that no caller checks.
            divergent.append(path)

    base = {
        "head": head,
        "target": target,
        "absent": absent,
        "divergent": divergent,
        "identical": identical,
        "dirty": dirty,
    }

    if head == target and not dirty:
        base.update(rc=RC_PINNED, verdict="PINNED",
                    reason="worktree HEAD is origin/main and no sentinel tool is edited")
        return base

    if head == target and dirty:
        base.update(rc=RC_DRIFTED, verdict="DRIFTED", reason=(
            "worktree HEAD is origin/main but %d sentinel tool(s) have UNCOMMITTED "
            "edits -- python runs the file on disk, not the commit" % len(dirty)))
        return base

    # Say it explicitly so nobody reads an empty divergent set as safety.
    base.update(rc=RC_DRIFTED, verdict="DRIFTED", reason=(
        "worktree HEAD is not origin/main; %d tool(s) would run SILENTLY at the "
        "wrong version, %d would fail loudly as missing, %d edited uncommitted"
        % (len(divergent), len(absent), len(dirty))))
    return base


# ------------------------------------------------------------------------ git plumbing
def _git(worktree: str, *args: str) -> Tuple[int, str]:
    try:
        p = subprocess.run(
            ["git", "-C", worktree, *args],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # pragma: no cover - transport
        return 2, str(exc)
    return p.returncode, (p.stdout or "").strip()


def _rev(worktree: str, ref: str, runner: Callable[..., Tuple[int, str]] = _git) -> Optional[str]:
    rc, out = runner(worktree, "rev-parse", ref)
    return out if rc == 0 and out else None


def _blob(
    worktree: str, commit: Optional[str], path: str,
    runner: Callable[..., Tuple[int, str]] = _git,
) -> Optional[str]:
    if not commit:
        return None
    rc, out = runner(worktree, "rev-parse", "%s:%s" % (commit, path))
    return out if rc == 0 and out else None


def _dirty(worktree: str, runner: Callable[..., Tuple[int, str]] = _git) -> List[str]:
    """Sentinel tools with uncommitted working-tree changes, per `git status`.

    DO NOT PARSE THIS BY COLUMN OFFSET. Porcelain v1 emits a two-character status
    field, so an unstaged modification is `" M tools/accept_gate.py"` with a LEADING
    SPACE -- and `_git` strips its output, which eats that space and shifts every
    column by one. The first version of this function used `line[3:]` and therefore
    read `"ols/accept_gate.py"`, matched nothing, and reported a genuinely edited
    accept_gate.py as clean. Its unit test passed, because the fixture supplied the
    UNSTRIPPED shape that the real runner never produces: a fixture that does not
    reproduce the runtime's transformation cannot disagree with you.

    So: match by SUFFIX against the known tool list. That is immune to the leading
    column, to `->` rename arrows, to quoting, and to backslash separators.
    """
    rc, out = runner(worktree, "status", "--porcelain", "--", *SENTINEL_TOOLS)
    if rc != 0 or not out:
        return []
    lines = [ln.replace("\\", "/").strip().strip('"') for ln in out.splitlines() if ln.strip()]
    return [tool for tool in SENTINEL_TOOLS if any(ln.endswith(tool) for ln in lines)]


def inspect(
    worktree: str = DEFAULT_WORKTREE,
    target_ref: str = DEFAULT_TARGET,
    fetch: bool = False,
    runner: Callable[..., Tuple[int, str]] = _git,
) -> dict:
    if not os.path.isdir(worktree):
        r = classify(None, None, {})
        r["reason"] = "worktree path does not exist: %s" % worktree
        r["worktree"] = worktree
        return r
    if fetch:
        runner(worktree, "fetch", "origin", "main", "-q")
    head = _rev(worktree, "HEAD", runner)
    target = _rev(worktree, target_ref, runner)
    blobs = {
        p: (_blob(worktree, head, p, runner), _blob(worktree, target, p, runner))
        for p in SENTINEL_TOOLS
    }
    r = classify(head, target, blobs, dirty=_dirty(worktree, runner))
    r["worktree"] = worktree
    r["target_ref"] = target_ref
    return r


def heal(
    worktree: str = DEFAULT_WORKTREE,
    target_ref: str = DEFAULT_TARGET,
    runner: Callable[..., Tuple[int, str]] = _git,
    force: bool = False,
) -> dict:
    """Re-detach the worktree to target and re-classify. Idempotent, converging.

    REFUSES on a SHARED worktree unless `force=True`. Healing a tree you share is
    the same act that caused the incident this tool exists to detect: on 2026-07-30
    prod-drift parked `_runbook` at the candidate sha at 09:39:49Z to gate it, and a
    heal inside that window would have swapped the tree under a running prod gate --
    silently, because `accept_gate.py` exists at BOTH shas. Fixing contention by
    adding a second mutator makes the race worse.

    A refusal deliberately KEEPS the DRIFTED rc. Declining to repair is not a
    repair, and a refusal that reported PINNED would be the exact "gate that skips
    reads as a gate that passes" defect.
    """
    before = inspect(worktree, target_ref, fetch=True, runner=runner)
    if before["rc"] == RC_UNKNOWN:
        before["healed"] = False
        return before
    if not force and _is_shared(worktree):
        before["healed"] = False
        before["refused"] = True
        before["reason"] = (
            "%s; REFUSED to heal: %s is SHARED. Give this lane its own worktree "
            "(python tools/lane_worktree.py --ensure <lane>) or pass --force if you "
            "own every reader right now." % (before.get("reason", ""), worktree)
        )
        return before
    rc, out = runner(worktree, "checkout", "--detach", target_ref, "-q")
    after = inspect(worktree, target_ref, fetch=False, runner=runner)
    after["healed"] = True
    after["head_before_heal"] = before["head"]
    if rc != 0:
        after["heal_error"] = out
    return after


def resolve_worktree(
    explicit=None,
    cwd=None,
    runner=_git,
):
    """Decide WHICH worktree to measure, and return the BASIS alongside it (R1).

    Why this exists (FU-202, 2026-07-30). `DEFAULT_WORKTREE` is the hardcoded
    shared tree `D:\\zo\\_runbook`. Before lane isolation (#2416) that was the only
    tree lanes ran from, so a cwd-independent default was right. After it, the
    default is the one path a lane is told NOT to use -- so

        cd D:\\zo\\_lanes\\prod-fire && python tools/runbook_pin.py --heal

    read as "prove THIS tree is pinned", silently measured `_runbook` instead,
    found it shared, refused to heal, and returned rc=1. That exact command had
    already been written into the prod one-click as the step immediately before
    `accept_gate` -- so the tool whose whole job is to certify WHICH accept_gate
    you are about to run would have blocked the fire while answering about a
    different tree. Measured both ways on 2026-07-30: bare -> DRIFTED rc=1 on
    `_runbook` (0 divergent); `--worktree <lane>` -> PINNED rc=0.

    An explicit `--worktree` always wins, so every existing caller is unchanged.
    Otherwise the answer is the git worktree ENCLOSING the cwd -- which is what
    "which copy do you actually run?" means -- and only if the cwd is not inside
    one do we fall back to the historical constant. The basis is RETURNED rather
    than printed so it reaches `--json` too: a resolution you cannot name is one
    you have not made.
    """
    if explicit:
        return explicit, "explicit --worktree"
    probe = cwd or os.getcwd()
    rc, out = runner(probe, "rev-parse", "--show-toplevel")
    if rc == 0 and out:
        return os.path.normpath(out.splitlines()[0].strip()), (
            "enclosing git worktree of cwd %s" % probe)
    return DEFAULT_WORKTREE, (
        "fallback DEFAULT_WORKTREE -- cwd %s is not inside a git worktree" % probe)


# ------------------------------------------------------------------------------- cli
def _render(r: dict) -> None:
    print("worktree : %s" % r.get("worktree"))
    if r.get("worktree_basis"):
        print("resolved : %s" % r["worktree_basis"])
    print("HEAD     : %s" % (r.get("head") or "<unresolved>"))
    print("target   : %s -> %s" % (r.get("target_ref") or DEFAULT_TARGET,
                                   r.get("target") or "<unresolved>"))
    if r.get("head_before_heal") and r["head_before_heal"] != r.get("head"):
        print("healed   : %s -> %s" % (r["head_before_heal"], r.get("head")))
    if r["absent"]:
        print("absent   : %s  (these fail LOUDLY -- a caller notices)"
              % ", ".join(r["absent"]))
    if r["divergent"]:
        print("DIVERGENT: %s  (these RUN, SILENTLY, AT THE WRONG VERSION)"
              % ", ".join(r["divergent"]))
    if r.get("dirty"):
        print("DIRTY    : %s  (uncommitted edits -- python runs the DISK, not the commit)"
              % ", ".join(r["dirty"]))
    if not r["absent"] and not r["divergent"] and not r.get("dirty") and r["rc"] != RC_UNKNOWN:
        print("tools    : all %d sentinel tools byte-identical to target"
              % len(r["identical"]))
    print("VERDICT  : %s rc=%d -- %s" % (r["verdict"], r["rc"], r["reason"]))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--worktree", default=None,
                    help="worktree to measure; default = the git worktree "
                         "enclosing the cwd, else " + DEFAULT_WORKTREE)
    ap.add_argument("--target", default=DEFAULT_TARGET)
    ap.add_argument("--fetch", action="store_true",
                    help="git fetch origin main before comparing")
    ap.add_argument("--heal", action="store_true",
                    help="re-detach the worktree to target and re-verify (idempotent)")
    ap.add_argument("--force", action="store_true",
                    help="heal even a SHARED worktree -- only if you own every reader")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    worktree, basis = resolve_worktree(a.worktree)
    r = (heal(worktree, a.target, force=a.force) if a.heal
         else inspect(worktree, a.target, a.fetch))
    r["worktree_basis"] = basis
    if a.json:
        print(json.dumps(r, indent=2, sort_keys=True))
    else:
        _render(r)
    return int(r["rc"])


if __name__ == "__main__":
    sys.exit(main())
