"""
CLI for the PR publisher.

    python -m zo_sentinel.publisher status     # enabled? how many unpublished?
    python -m zo_sentinel.publisher run-once   # one pass (dry-run unless enabled)

Live runs use HttpMeshStore (write_service) + CliGitOps against a host clone set
via PR_PUBLISHER_CLONE_DIR. Dormant unless `.pr_publisher_enabled` exists.
"""
from __future__ import annotations

import json
import os
import sys

from zo_sentinel.ingestor.store import HttpMeshStore
from zo_sentinel.publisher.gitops import CliGitOps, FakeGitOps
from zo_sentinel.publisher.publisher import Publisher


def _make_publisher() -> Publisher:
    store = HttpMeshStore()
    clone = os.environ.get("PR_PUBLISHER_CLONE_DIR")
    gitops = CliGitOps(clone) if clone else FakeGitOps()
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
