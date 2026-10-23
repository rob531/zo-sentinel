#!/usr/bin/env python3
"""Measure -- and drain -- the gap between the builder's ON-DISK output and
what the shipping ref (origin/main) actually carries.

WHY THIS EXISTS
---------------
`goose_runner.py` contains no git code at all: it writes generated service
files straight to the build host's working tree. Publishing is a SEPARATE
path (`zo_sentinel/publisher/publisher.py`) which reads build artifacts out
of the mesh store and pushes each one through the GitHub API. Six outcomes
in `Publisher.run_once` -- `hollow_blocked`, `duplicate_module`,
`saturated_family`, safety `blocked`, `quarantined`, and
`skip: content unresolved/empty` -- advance the watermark and are never
retried. Each one leaves the file sitting on disk, permanently unpublished.

Nothing anywhere compared the two. Measured 2026-09-27: origin/main carried
1,571 `services/staged/**.py` (318 routers); the live host carried 6,505
(1,615 routers). 4,934 files -- oldest written 2026-07-26, 63 days -- existed
only on the build host: in no commit, no PR, and not in the product.

That divergence is also why chairman issue #4002 produced three mutually
contradictory populations (25 / 3 / 19): measurements taken on the host and
measurements taken on a clean worktree of origin/main were reading two
different codebases, and neither side noticed the trees had diverged.

Doctrine notes:
  R1  the ref is resolved with `git rev-parse` at call time, never assumed.
  R5  every number prints its BASIS: repo, ref, ref sha, roots, UTC clock.
  R6  a ref that cannot be resolved exits 2 (UNKNOWN) -- never 0, never "no gap".

EXIT CODES
  0  no gap, or --emit completed (including an idempotent no-op)
  1  gap present at census
  2  refused or could not measure -- NEVER read as a pass
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import subprocess
import sys

DEFAULT_ROOTS = ("services/staged", "services/active")
DEFAULT_EXTS = (".py", ".toml")
SKIP_PARTS = ("__pycache__", ".git", ".ipynb_checkpoints")
PROTECTED_BRANCHES = ("main", "master")


def _git(repo, *args, check=True):
    p = subprocess.run(["git", "-C", repo, *args],
                       capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError("git %s -> rc=%s: %s"
                           % (" ".join(args), p.returncode, p.stderr.strip()))
    return p.stdout


def resolve_ref(repo, ref):
    """R1: resolve the comparison ref from git, at call time."""
    try:
        return _git(repo, "rev-parse", "--verify", "%s^{commit}" % ref).strip()
    except RuntimeError:
        return None


def ref_files(repo, ref, roots):
    out = _git(repo, "ls-tree", "-r", "--name-only", ref)
    keep = set()
    for line in out.splitlines():
        line = line.strip()
        if any(line == r or line.startswith(r + "/") for r in roots):
            keep.add(line)
    return keep


def disk_files(repo, roots, exts):
    keep = set()
    for root in roots:
        base = os.path.join(repo, root)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in SKIP_PARTS]
            for fn in filenames:
                if exts and not fn.endswith(tuple(exts)):
                    continue
                full = os.path.join(dirpath, fn)
                rel = os.path.relpath(full, repo).replace(os.sep, "/")
                if any(part in SKIP_PARTS for part in rel.split("/")):
                    continue
                keep.add(rel)
    return keep


def measure(repo, ref, roots, exts):
    sha = resolve_ref(repo, ref)
    if sha is None:
        return None
    on_ref = ref_files(repo, ref, roots)
    on_disk = disk_files(repo, roots, exts)
    on_ref_cmp = {p for p in on_ref if not exts or p.endswith(tuple(exts))}
    missing = sorted(on_disk - on_ref_cmp)
    return {
        "basis": {
            "repo": os.path.abspath(repo),
            "ref": ref,
            "ref_sha": sha,
            "roots": list(roots),
            "exts": list(exts),
            "measured_at_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "host": os.uname().nodename if hasattr(os, "uname") else "?",
        },
        "on_ref": len(on_ref_cmp),
        "on_disk": len(on_disk),
        "missing_from_ref": len(missing),
        "missing": missing,
    }


def _by_service(paths):
    groups = {}
    for p in paths:
        parts = p.split("/")
        key = "/".join(parts[:3]) if len(parts) >= 3 else "/".join(parts[:-1])
        groups.setdefault(key, []).append(p)
    return groups


def cmd_census(args):
    res = measure(args.repo, args.ref, args.roots, args.exts)
    if res is None:
        sys.stderr.write(
            "UNKNOWN: cannot resolve ref %r in %s. Unknown is not zero (R6); "
            "exiting 2, which is never a pass.\n" % (args.ref, args.repo))
        return 2
    if args.json:
        out = dict(res)
        if not args.list:
            out.pop("missing")
        print(json.dumps(out, indent=2))
    else:
        b = res["basis"]
        print("BASIS  repo=%s" % b["repo"])
        print("       ref=%s (%s)  roots=%s  exts=%s"
              % (b["ref"], b["ref_sha"][:9], ",".join(b["roots"]),
                 ",".join(b["exts"])))
        print("       measured %s on %s" % (b["measured_at_utc"], b["host"]))
        print("on ref  : %d" % res["on_ref"])
        print("on disk : %d" % res["on_disk"])
        print("MISSING FROM REF: %d" % res["missing_from_ref"])
        if res["missing_from_ref"]:
            groups = _by_service(res["missing"])
            print("  spread over %d director(ies)" % len(groups))
            if args.list:
                for p in res["missing"]:
                    print("  %s" % p)
            else:
                for p in res["missing"][:10]:
                    print("  %s" % p)
                if res["missing_from_ref"] > 10:
                    print("  ... (%d more; --list for all)"
                          % (res["missing_from_ref"] - 10))
    return 1 if res["missing_from_ref"] else 0


def cmd_emit(args):
    branch = _git(args.repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
    if branch in PROTECTED_BRANCHES:
        sys.stderr.write(
            "REFUSED: HEAD is %r. This tool never commits to a protected "
            "branch -- check out a work branch first.\n" % branch)
        return 2
    res = measure(args.repo, args.ref, args.roots, args.exts)
    if res is None:
        sys.stderr.write("UNKNOWN: cannot resolve ref %r; refusing to emit.\n"
                         % args.ref)
        return 2
    if not res["missing_from_ref"]:
        print("nothing to do: 0 files missing from %s (idempotent no-op)"
              % args.ref)
        return 0

    groups = _by_service(res["missing"])
    batch, svcs = [], []
    for key in sorted(groups):
        if args.limit and len(batch) + len(groups[key]) > args.limit and batch:
            break
        batch.extend(sorted(groups[key]))
        svcs.append(key)
        if args.limit and len(batch) >= args.limit:
            break

    print("would add %d file(s) across %d director(ies)"
          % (len(batch), len(svcs)))
    for p in batch[:20]:
        print("  + %s" % p)
    if len(batch) > 20:
        print("  ... (%d more)" % (len(batch) - 20))
    if args.dry_run:
        print("DRY RUN: nothing staged, nothing committed.")
        return 0

    _git(args.repo, "add", "--", *batch)
    staged = _git(args.repo, "diff", "--cached", "--name-status").splitlines()
    bad = [s for s in staged if not s.startswith("A")]
    if bad:
        _git(args.repo, "reset", "-q")
        sys.stderr.write(
            "REFUSED: the index held %d non-add change(s); this tool only ever "
            "ADDS files. Index reset, nothing committed.\n" % len(bad))
        return 2
    msg = (args.message
           or "restage: %d builder-written file(s) across %d service dir(s)"
              % (len(batch), len(svcs)))
    _git(args.repo, "commit", "-q", "-m", msg)
    head = _git(args.repo, "rev-parse", "--short", "HEAD").strip()
    print("committed %s on %s (%d file(s))" % (head, branch, len(batch)))
    return 0


def cmd_selftest(args):
    """NEGATIVE CONTROL, built in and re-runnable (R4).

    An assertion never observed RED is not evidence. This drives the census
    through green -> RED -> green in a throwaway repo, so any later lane can
    prove in one command that the detector actually discriminates.
    """
    import tempfile
    import shutil
    tmp = tempfile.mkdtemp(prefix="staged_reconcile_selftest_")
    try:
        _git(tmp, "init", "-q", "-b", "main")
        _git(tmp, "config", "user.email", "selftest@local")
        _git(tmp, "config", "user.name", "selftest")
        svc = os.path.join(tmp, "services", "staged", "demo_service")
        os.makedirs(svc)
        with open(os.path.join(svc, "__init__.py"), "w") as fh:
            fh.write("# committed\n")
        _git(tmp, "add", "-A")
        _git(tmp, "commit", "-q", "-m", "base")

        roots, exts = ["services/staged"], [".py"]
        rows = []

        r1 = measure(tmp, "HEAD", roots, exts)
        rows.append(("POLE 1  clean tree, nothing untracked",
                     r1["missing_from_ref"], 0))

        with open(os.path.join(svc, "router.py"), "w") as fh:
            fh.write("# written by the builder, never published\n")
        r2 = measure(tmp, "HEAD", roots, exts)
        rows.append(("POLE 2  one builder-written file on disk only",
                     r2["missing_from_ref"], 1))

        _git(tmp, "add", "-A")
        _git(tmp, "commit", "-q", "-m", "restage")
        r3 = measure(tmp, "HEAD", roots, exts)
        rows.append(("POLE 3  same file, now on the ref",
                     r3["missing_from_ref"], 0))

        r4 = measure(tmp, "refs/heads/no-such-branch", roots, exts)
        rows.append(("POLE 4  unresolvable ref -> UNKNOWN, not 0",
                     "UNKNOWN" if r4 is None else r4["missing_from_ref"],
                     "UNKNOWN"))

        ok = True
        print("NEGATIVE CONTROL -- staged_repo_reconcile census")
        for label, got, want in rows:
            good = (got == want)
            ok = ok and good
            print("  [%s] %-46s got=%s want=%s"
                  % ("ok" if good else "FAIL", label, got, want))
        print("VERDICT: %s" % ("all poles held" if ok else "CONTROL FAILED"))
        return 0 if ok else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Measure and drain the disk-vs-ref gap in builder output.")
    ap.add_argument("--repo", default=".", help="working tree to census")
    ap.add_argument("--ref", default="origin/main",
                    help="shipping ref to compare against (resolved at call "
                         "time, R1)")
    ap.add_argument("--roots", nargs="*", default=list(DEFAULT_ROOTS))
    ap.add_argument("--exts", nargs="*", default=list(DEFAULT_EXTS))
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--list", action="store_true",
                    help="print every missing path")
    ap.add_argument("--emit", action="store_true",
                    help="stage and commit a bounded batch of the missing "
                         "files onto the CURRENT branch (adds only; never "
                         "deletes, never touches a tracked file)")
    ap.add_argument("--limit", type=int, default=200,
                    help="max files per --emit commit (0 = no bound)")
    ap.add_argument("--message", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--selftest", action="store_true",
                    help="run the built-in negative control")
    args = ap.parse_args(argv)

    if args.selftest:
        return cmd_selftest(args)
    if args.emit:
        return cmd_emit(args)
    return cmd_census(args)


if __name__ == "__main__":
    sys.exit(main())
