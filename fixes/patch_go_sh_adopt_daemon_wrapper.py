#!/usr/bin/env python3
"""
patch_go_sh_adopt_daemon_wrapper.py  -- commit 3.4

Replace bare-nohup launches in go.sh with daemon_wrapper.sh launches for
the three nohup daemons that have been silently dying when write_service
crashes:

  - zo_sentinel_builder (section 12)
  - sentinel_directive_generator (section 12.5)
  - gate_scheduler (section 12.6)

The wrapper (daemon_wrapper.sh) auto-respawns the daemon on non-zero
exit with exponential backoff and a rate-limit ceiling. Clean exits
(rc=0) are respected -- wrapper stops along with the daemon.

This patcher:
  - Guards with marker check so re-runs are safe
  - Backs up go.sh with timestamped suffix before writing
  - Uses minimal, whitespace-tolerant anchors so it doesn't drift
    when you edit go.sh between runs

Idempotent. No AST (bash file); syntax-checked via bash -n after.
"""
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_mesh/go.sh")
MARKER = "daemon_wrapper.sh zo_sentinel_builder"


# Three anchors: one per daemon. Each is the full original nohup line
# from go.sh v2.6. Replacement uses daemon_wrapper.sh so crashes respawn.

BUILDER_OLD = (
    'nohup python3 $MESH/zo_sentinel_builder.py '
    '>> $LOGS/zo_sentinel_builder.log 2>&1 &'
)
BUILDER_NEW = (
    'nohup bash $MESH/daemon_wrapper.sh zo_sentinel_builder '
    '$SENTINEL/zo_sentinel_builder.py '
    '>> $LOGS/zo_sentinel_builder.log 2>&1 &'
)

DIRGEN_OLD = (
    'nohup python3 $SENTINEL/sentinel_directive_generator.py '
    '>> $LOGS/sentinel_sentinel_directive_generator.log 2>&1 &'
)
DIRGEN_NEW = (
    'nohup bash $MESH/daemon_wrapper.sh sentinel_directive_generator '
    '$SENTINEL/sentinel_directive_generator.py '
    '>> $LOGS/sentinel_sentinel_directive_generator.log 2>&1 &'
)

GATESCHED_OLD = (
    'nohup python3 $SENTINEL/gate_scheduler.py '
    '>> $LOGS/gate_scheduler.log 2>&1 &'
)
GATESCHED_NEW = (
    'nohup bash $MESH/daemon_wrapper.sh gate_scheduler '
    '$SENTINEL/gate_scheduler.py '
    '>> $LOGS/gate_scheduler.log 2>&1 &'
)

# Also update the kill-list to target daemon_wrapper processes too,
# so 'zm go' cleans them up before restarting. Otherwise rerunning
# go.sh leaves orphan wrappers.

KILL_OLD = (
    'pkill -f \'write_service_wrapper\' 2>/dev/null || true'
)
KILL_NEW = (
    'pkill -f \'write_service_wrapper\' 2>/dev/null || true\n'
    'pkill -f \'daemon_wrapper.sh\' 2>/dev/null || true'
)


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main():
    print("=" * 60)
    print("go.sh: adopt daemon_wrapper.sh for 3 nohup daemons (commit 3.4)")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2

    src = TARGET.read_text()

    if MARKER in src:
        print("  [skip] wrapper adoption already applied")
        return 0

    patches = [
        ("A", "builder launch -> wrapper",     BUILDER_OLD,    BUILDER_NEW),
        ("B", "directive_gen launch -> wrapper", DIRGEN_OLD,   DIRGEN_NEW),
        ("C", "gate_scheduler launch -> wrapper", GATESCHED_OLD, GATESCHED_NEW),
        ("D", "kill-list adds daemon_wrapper.sh", KILL_OLD,     KILL_NEW),
    ]

    changed = False
    for label, desc, old, new in patches:
        if old not in src:
            print(f"  [FAIL {label}] {desc}: anchor not found verbatim")
            print(f"          expected: {old[:80]}...")
            return 2
        src = src.replace(old, new, 1)
        print(f"  [patch {label}] {desc}")
        changed = True

    if not changed:
        print("  [noop] nothing to do")
        return 0

    # Validate bash syntax BEFORE committing
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
    print(f"\n  [done] go.sh patched; {TARGET.name}")
    print("\nVerify:")
    print("  bash -n /home/workspace/zo_mesh/go.sh && echo 'bash OK'")
    print("  chmod +x /home/workspace/zo_mesh/daemon_wrapper.sh")
    print("\nTo deploy (restarts the three daemons under wrapper supervision):")
    print("  cd /home/workspace/zo_mesh && bash go.sh")
    print("\nVerify wrapper is active (after zm go):")
    print("  pgrep -f daemon_wrapper.sh | wc -l   # expect 3")
    print("  tail -f /home/workspace/logs/wrapper_zo_sentinel_builder.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())