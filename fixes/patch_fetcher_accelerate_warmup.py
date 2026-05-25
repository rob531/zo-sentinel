#!/usr/bin/env python3
"""
patch_fetcher_accelerate_warmup.py  -- one-shot warmup accelerator

Temporary patch: drops ecosystems_metadata_fetcher's CYCLE_INTERVAL_S
from 6h (21600s) to 5min (300s) so we can complete full 790-server
warmup in ~80 minutes tonight instead of 4 days.

At 50 req per 5min = 600 req/hr. Well under ecosyste.ms 5000/hr limit.

Why this matters for Commit B: canonicalizer uses STICKY logic -- once
assigned, canonical_id resists change without governance event. If we
run canonicalizer with only 13% coverage, 87% of servers get locked
into 'pkg:self/*' which would then require manual drift-promotion
when real metadata arrives.

Better to warm the cache first, then canonicalize once against full data.

Idempotent via marker.

RESTORE: after full warmup, there's a sibling patcher
patch_fetcher_restore_normal_cycle.py that flips back to 6h cycles.
Call that before leaving the fetcher running indefinitely at 5min.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_sentinel/ecosystems_metadata_fetcher.py")
OLD = "CYCLE_INTERVAL_S = 6 * 3600  # 6 hours"
NEW = "CYCLE_INTERVAL_S = 300  # 5 min (WARMUP MODE -- restore to 6*3600 via patch_fetcher_restore_normal_cycle.py)"


def main():
    print("=" * 60)
    print("patch_fetcher_accelerate_warmup.py")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target missing: {TARGET}")
        return 2
    src = TARGET.read_text()

    if "CYCLE_INTERVAL_S = 300" in src:
        print("  [skip] already in warmup mode")
        return 0
    if OLD not in src:
        print("  [FAIL] CYCLE_INTERVAL_S anchor not found -- may already be modified")
        return 2

    src = src.replace(OLD, NEW, 1)
    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"  [FAIL] AST invalid: {e}")
        return 2

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bak = TARGET.with_suffix(f".py.bak.{ts}")
    shutil.copy2(TARGET, bak)
    TARGET.write_text(src)

    print(f"  [backup] {bak.name}")
    print("  [patch] CYCLE_INTERVAL_S: 21600 -> 300")
    print()
    print("Restart fetcher to pick up warmup cycle:")
    print("  pkill -f 'daemon_wrapper.sh ecosystems_metadata_fetcher'")
    print("  sleep 2")
    print("  source /home/workspace/zo_mesh/.zo_env")
    print("  nohup bash /home/workspace/zo_mesh/daemon_wrapper.sh \\")
    print("    ecosystems_metadata_fetcher \\")
    print("    /home/workspace/zo_sentinel/ecosystems_metadata_fetcher.py \\")
    print("    >> /home/workspace/logs/ecosystems_metadata_fetcher.log 2>&1 &")
    print()
    print("Monitor progress:")
    print("  watch -n 30 'curl -s http://127.0.0.1:8772/query \\")
    print("    -H \"Content-Type: application/json\" \\")
    print("    -d {\\\\\"sql\\\\\":\\\\\"SELECT COUNT(*) FROM mcp_ecosystems_metadata\\\\\"}'")
    print()
    print("Full warmup target: ~80 minutes for 790 servers (14 more cycles)")
    print("Once complete, RESTORE via:")
    print("  python3 /home/workspace/zo_sentinel/fixes/patch_fetcher_restore_normal_cycle.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())