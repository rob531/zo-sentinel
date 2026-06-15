"""
CLI for the PR publisher.

    python -m zo_sentinel.publisher status     # enabled? how many unpublished?
    python -m zo_sentinel.publisher run-once   # one pass (dry-run unless enabled)

Live runs use HttpMeshStore (write_service) + CliGitOps against a host clone set
via PR_PUBLISHER_CLONE_DIR (defaulting to /home/workspace/zo_sentinel_pub_clone).
Dormant unless `.pr_publisher_enabled` exists.
"""
from __future__ import annotations

import json
import os
import sys

from zo_sentinel.ingestor.store import HttpMeshStore
from zo_sentinel.publisher.gitops import CliGitOps, FakeGitOps
from zo_sentinel.publisher.publisher import Publisher

# Standard host clone for REAL (CliGitOps) publishing. Used when
# PR_PUBLISHER_CLONE_DIR is unset so a bare relaunch (the janitor/launcher runs
# `python3 -m zo_sentinel.publisher run-once` with no env) still publishes REAL
# PRs instead of silently degrading to FakeGitOps -- the fake-mode regression
# that opened pull/FAKE URLs, advanced the publish watermark, and SILENTLY LOST
# build_artifacts (real PRs stalled at #172 on 2026-06-15 until the publisher was
# relaunched with the env). A hardcoded default cannot "drop" on relaunch.
DEFAULT_CLONE_DIR = "/home/workspace/zo_sentinel_pub_clone"


def _resolve_clone_dir() -> str | None:
    """Clone dir for CliGitOps: explicit env wins; else the standard host clone
    if it exists; else None (caller falls back to FakeGitOps, loudly)."""
    clone = os.environ.get("PR_PUBLISHER_CLONE_DIR")
    if clone:
        return clone
    if os.path.isdir(DEFAULT_CLONE_DIR):
        return DEFAULT_CLONE_DIR
    return None


def _make_publisher() -> Publisher:
    store = HttpMeshStore()
    clone = _resolve_clone_dir()
    if clone:
        gitops = CliGitOps(clone)
    else:
        # No clone dir anywhere -> FakeGitOps would open fake PRs AND advance the
        # watermark (silent artifact loss). Keep the fallback so the process does
        # not crash-loop, but make the degraded state LOUD instead of silent.
        sys.stderr.write(
            "[publisher] WARN: no clone dir -- PR_PUBLISHER_CLONE_DIR unset and "
            f"default {DEFAULT_CLONE_DIR} missing. Using FakeGitOps (DRY-RUN, NO "
            "real PRs). Set PR_PUBLISHER_CLONE_DIR or create the clone.\n")
        sys.stderr.flush()
        gitops = FakeGitOps()
    cap = int(os.environ.get("PR_PUBLISHER_DAILY_CAP", "8"))
    spacing = float(os.environ.get("PR_PUBLISHER_PR_SPACING_SEC", "5"))
    return Publisher(store, gitops=gitops, daily_cap=cap, pr_spacing_sec=spacing)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "status"
    pub = _make_publisher()

    if cmd == "status":
        results = pub.run_once()  # dry-run when dormant; reports plans
        enabled = pub.is_enabled()
        bud_day, bud_count = pub._load_budget()
        print(f"enabled:           {enabled}")
        print(f"gitops:            {type(pub.gitops).__name__}")
        print(f"watermark:         {pub._load_watermark() or '(unset -- seed before enabling)'}")
        print(f"daily_cap:         {pub.daily_cap}  (used today {bud_day}: {bud_count})")
        print(f"pr_spacing_sec:    {pub.pr_spacing_sec}")
        print(f"unpublished/plans: {len(results)}")
        for r in results[:10]:
            print(f"  [{r['action']}] {r.get('file')}  tier={r.get('tier','-')}")
        return 0

    if cmd == "run-once":
        results = pub.run_once()
        print(json.dumps(results, indent=2))
        return 0

    print(f"unknown command: {cmd!r} (use: status | run-once)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
