#!/usr/bin/env python3
"""
patch_fetcher_restore_normal_cycle.py

Reverses patch_fetcher_accelerate_warmup.py -- flips CYCLE_INTERVAL_S
back from 5min warmup mode to 6h steady-state cycles.

Run this once the full 790-server cache is warm. Running the fetcher
at 5-min cycles long-term would waste API quota re-fetching servers
whose 24h cache hasn't expired yet.
"""
import ast
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_sentinel/ecosystems_metadata_fetcher.py")
OLD = "CYCLE_INTERVAL_S = 300  # 5 min (WARMUP MODE -- restore to 6*3600 via patch_fetcher_restore_normal_cycle.py)"
NEW = "CYCLE_INTERVAL_S = 6 * 3600  # 6 hours"


def main():
    print("patch_fetcher_restore_normal_cycle.py")
    print("=" * 50)
    if not TARGET.exists():
        print("  [FAIL] missing target")
        return 2
    src = TARGET.read_text()
    if "CYCLE_INTERVAL_S = 6 * 3600" in src and "WARMUP MODE" not in src:
        print("  [skip] already in normal mode")
        return 0
    if OLD not in src:
        print("  [FAIL] warmup anchor not found")
        return 2
    src = src.replace(OLD, NEW, 1)
    try:
        ast.parse(src)
    except SyntaxError as e:
        print(f"  [FAIL] AST invalid: {e}")
        return 2

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    shutil.copy2(TARGET, TARGET.with_suffix(f".py.bak.{ts}"))
    TARGET.write_text(src)
    print("  [patch] 5min -> 6h")
    print("  Restart fetcher: pkill -f ecosystems_metadata_fetcher && source .zo_env && nohup ...")
    return 0


if __name__ == "__main__":
    sys.exit(main())