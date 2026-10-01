"""
CLI for the producer's publish path (RCA 2026-10 Fix 3; the watermark path is retired).

    python -m zo_sentinel.publisher status        # outbox counts, enabled?, clone?
    python -m zo_sentinel.publisher run-once      # drain pending builds (+ backfill if enabled)
    python -m zo_sentinel.publisher census        # host tree vs origin/main (rc 1 = gap)
    python -m zo_sentinel.publisher evict-held    # chairman-ruled: evict refused backlog files

`run-once` keeps its name so tools/run_publisher_daemon.sh needs no edit: the
daemon loop now drains the producer's durable outbox instead of reading build
rows behind a watermark. With no real clone it REFUSES (rc 2) and leaves every
entry pending -- the FakeGitOps fallback that once marked real builds published
is gone.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from zo_sentinel.publisher.gitops import CliGitOps
from zo_sentinel.publisher.publisher import (
    BACKFILL_SENTINEL,
    DEFAULT_HOME,
    ProducerCommit,
)

DEFAULT_CLONE_DIR = "/home/workspace/zo_sentinel_pub_clone"


def resolve_clone_dir() -> str | None:
    """Explicit env wins; else the standard host clone if it exists; else None."""
    clone = os.environ.get("PR_PUBLISHER_CLONE_DIR")
    if clone:
        return clone
    if os.path.isdir(DEFAULT_CLONE_DIR):
        return DEFAULT_CLONE_DIR
    return None


def make_producer(home: str = DEFAULT_HOME) -> ProducerCommit:
    clone = resolve_clone_dir()
    return ProducerCommit(
        gitops=CliGitOps(clone) if clone else None,
        home=home,
        daily_cap=int(os.environ.get("PR_PUBLISHER_DAILY_CAP", "100")),
        pr_spacing_sec=float(os.environ.get("PR_PUBLISHER_PR_SPACING_SEC", "5")),
    )


def _census(home: str) -> dict | None:
    sys.path.insert(0, str(Path(home) / "tools"))
    import staged_repo_reconcile as srr
    from staged_repo_reconcile import DEFAULT_EXTS, DEFAULT_ROOTS
    return srr.measure(home, "origin/main", list(DEFAULT_ROOTS), list(DEFAULT_EXTS))


def _backfill_enabled(home: str) -> bool:
    env = os.environ.get("PR_BACKFILL_ENABLED")
    if env is not None:
        return env.strip().lower() in ("1", "true", "yes", "on")
    return (Path(home) / BACKFILL_SENTINEL).exists()


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    cmd = argv[0] if argv else "status"
    home = os.environ.get("ZO_SENTINEL_HOME", DEFAULT_HOME)
    pc = make_producer(home)

    if cmd == "status":
        print(json.dumps(pc.status(), indent=2))
        return 0

    if cmd == "census":
        res = _census(home)
        if res is None:
            print("UNKNOWN: origin/main did not resolve -- never a pass")
            return 2
        if "--list" not in argv:
            res.pop("missing", None)
        print(json.dumps(res, indent=2))
        return 1 if res["missing_from_ref"] else 0

    if cmd == "run-once":
        if pc.gitops is None:
            print("REFUSED: no publish clone (PR_PUBLISHER_CLONE_DIR unset and "
                  f"{DEFAULT_CLONE_DIR} missing). Every entry stays PENDING -- nothing "
                  "is marked published that was not.", file=sys.stderr)
            print(json.dumps(pc.status(), indent=2))
            return 2
        out = {"drained": pc.drain()}
        if _backfill_enabled(home):
            res = _census(home)
            if res is not None:
                out["backfill"] = pc.backfill(res["missing"],
                                              limit=int(os.environ.get("PR_BACKFILL_LIMIT", "20")))
        out["status"] = pc.status()
        print(json.dumps(out, indent=2))
        return 0

    if cmd == "evict-held":
        print(json.dumps(pc.evict_held(), indent=2))
        return 0

    print(f"unknown command: {cmd!r} (use: status | run-once | census | evict-held)",
          file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
