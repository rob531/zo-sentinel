#!/usr/bin/env python3
"""
seed_publisher_watermark.py -- set the PR publisher's watermark so that when you
enable it, it publishes only builds from NOW forward and never replays the
historical backlog.

The publisher reads `build_artifacts` with built_at > watermark (via
store.read_build_artifacts_since) and advances the watermark as it goes. With no
watermark it would start from the oldest artifact. This tool writes the watermark
to the current UTC time (default) or an explicit ISO timestamp, so the backlog is
skipped in one shot.

Run this ONCE on the host, then flip `.pr_publisher_enabled`:
    PYTHONPATH=/home/workspace/zo_sentinel python3 tools/seed_publisher_watermark.py
    PYTHONPATH=/home/workspace/zo_sentinel python3 tools/seed_publisher_watermark.py --at 2026-06-01T22:00:00+00:00
    PYTHONPATH=/home/workspace/zo_sentinel python3 tools/seed_publisher_watermark.py --show   # just print current

Read-only with --show; otherwise writes a single mesh_memory row via write_service.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone

from zo_sentinel.ingestor.store import HttpMeshStore
from zo_sentinel.publisher.publisher import (
    PUBLISHER_AGENT_ID,
    WATERMARK_TYPE,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--at", default=None,
                    help="ISO timestamp to seed (default: now, UTC)")
    ap.add_argument("--show", action="store_true",
                    help="print the current watermark and exit (no write)")
    args = ap.parse_args(argv)

    store = HttpMeshStore()
    current = store.read_latest(WATERMARK_TYPE, PUBLISHER_AGENT_ID)
    print(f"current publisher watermark: {current or '(unset)'}")
    if args.show:
        return 0

    value = args.at or datetime.now(timezone.utc).isoformat()
    ok = store.write("mesh_memory", {
        "agent_id": PUBLISHER_AGENT_ID,
        "memory_type": WATERMARK_TYPE,
        "content": value,
        "importance": 0.3,
    })
    if not ok:
        print(f"ERROR: write failed ({getattr(store, 'last_error', '?')})", file=sys.stderr)
        return 1
    print(f"seeded publisher watermark -> {value}")
    print("Publisher will now only PR builds AFTER this point. Safe to enable:")
    print("  export PR_PUBLISHER_CLONE_DIR=<clone>; touch /home/workspace/zo_sentinel/.pr_publisher_enabled")
    return 0


if __name__ == "__main__":
    sys.exit(main())
