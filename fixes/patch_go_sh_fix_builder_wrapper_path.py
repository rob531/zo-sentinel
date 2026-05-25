#!/usr/bin/env python3
"""
patch_go_sh_fix_builder_wrapper_path.py  -- commit 3.4 regression fix

Fix a path typo introduced by patch_go_sh_adopt_daemon_wrapper.py earlier
today. That patcher routed three daemons through daemon_wrapper.sh and
hardcoded all three as living under $SENTINEL. Two of them (directive_gen,
gate_scheduler) are correctly at $SENTINEL. But zo_sentinel_builder.py
actually lives under $MESH (per go.sh section 12 comment + verified on
disk at /home/workspace/zo_mesh/zo_sentinel_builder.py).

Result: wrapper has been logging \"script not found: /home/workspace/
zo_sentinel/zo_sentinel_builder.py\" and exiting, meaning the builder
hasn't been running since the last 'zm go'. Backlog of directives has
been piling up (three new external API seed directives land today and
won't get picked up until this is fixed).

One-line change: $SENTINEL -> $MESH for the builder's wrapper target.
Idempotent (marker-checked). bash -n validated before write.
"""
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_mesh/go.sh")

OLD = (
    "nohup bash $MESH/daemon_wrapper.sh zo_sentinel_builder "
    "$SENTINEL/zo_sentinel_builder.py "
    ">> $LOGS/zo_sentinel_builder.log 2>&1 &"
)
NEW = (
    "nohup bash $MESH/daemon_wrapper.sh zo_sentinel_builder "
    "$MESH/zo_sentinel_builder.py "
    ">> $LOGS/zo_sentinel_builder.log 2>&1 &"
)


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main():
    print("=" * 60)
    print("go.sh: fix builder wrapper path ($SENTINEL -> $MESH)")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2

    src = TARGET.read_text()

    # If the fixed form is already present AND the broken form isn't, we're done
    if NEW in src and OLD not in src:
        print("  [skip] builder wrapper path already points to $MESH")
        return 0

    if OLD not in src:
        print("  [FAIL] broken anchor not found verbatim; inspect go.sh manually")
        return 2

    src = src.replace(OLD, NEW, 1)
    print("  [patch] builder wrapper target: $SENTINEL -> $MESH")

    # bash -n validation before committing
    tmp = TARGET.with_suffix(".sh.candidate")
    tmp.write_text(src)
    try:
        result = subprocess.run(
            ["bash", "-n", str(tmp)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            print(f"  [FAIL] bash -n syntax error: {result.stderr}")
            tmp.unlink()
            return 2
    finally:
        if tmp.exists():
            tmp.unlink()

    _backup(TARGET)
    TARGET.write_text(src)
    print(f"\n  [done] go.sh patched")
    print("\nImmediate action (brings the builder back without full zm go):")
    print("  # Kill the broken wrapper loop")
    print("  pkill -f 'daemon_wrapper.sh zo_sentinel_builder'")
    print("  sleep 2")
    print("  # Relaunch manually with the correct path")
    print("  cd /home/workspace && source /home/workspace/zo_mesh/.zo_env")
    print("  nohup bash /home/workspace/zo_mesh/daemon_wrapper.sh zo_sentinel_builder \\")
    print("    /home/workspace/zo_mesh/zo_sentinel_builder.py \\")
    print("    >> /home/workspace/logs/zo_sentinel_builder.log 2>&1 &")
    print("\nVerify:")
    print("  sleep 5; tail /home/workspace/logs/zo_sentinel_builder.log")
    print("  # Expect: 'ZO-SENTINEL Builder' startup banner, then polling")
    print("  pgrep -f 'zo_sentinel_builder.py' | wc -l   # expect 1")
    print("\nWith builder alive, your 3 seed directives get picked up:")
    print("  ls /home/workspace/zo_sentinel/directives/seed_external_api_*.json")
    print("  # Should show 3 files; builder will process them on its next poll cycle")
    return 0


if __name__ == "__main__":
    sys.exit(main())