#!/usr/bin/env python3
"""
patch_go_sh_add_liveness_probe.py  -- commit 3.5

Add section 12.7 to go.sh that launches liveness_probe.py under the
daemon_wrapper. Must run AFTER 3.4 (wrapper adoption patcher).

Also extends the kill-list to include liveness_probe.py so zm go
cleans it up between runs.

Idempotent. Syntax-checks bash before writing.
"""
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_mesh/go.sh")
MARKER = "12.7 Liveness Probe"

# Insert new section after 12.6 (GateScheduler) and before section 13
OLD = (
    'hdr "12.6 Gate Scheduler (6h cadence, nohup)"'
)
# Rather than trying to match the whole 12.6 block, we'll insert BEFORE
# section 13 which has a unique anchor

ANCHOR_OLD = 'hdr "13. World Article Feeder"'
ANCHOR_NEW = (
    'hdr "12.7 Liveness Probe (60s polling, wrapper-managed)"\n'
    '# Lightweight service health poller. Writes liveness_observation records\n'
    '# to mesh_memory on status changes + every 10 cycles (~10min). Completely\n'
    '# independent of gate_scheduler and manager_agent. Logs forensic trace\n'
    '# to /home/workspace/logs/liveness_probe_forensic.log if write_service\n'
    '# itself becomes unreachable (critical for diagnosing DB crashes).\n'
    'nohup bash $MESH/daemon_wrapper.sh liveness_probe \\\n'
    '    $MESH/liveness_probe.py \\\n'
    '    >> $LOGS/liveness_probe.log 2>&1 &\n'
    'sleep 2\n'
    'LP=$(pgrep -f \'liveness_probe.py\' 2>/dev/null | head -1)\n'
    '[[ -n "$LP" ]] && ok "LivenessProbe PID $LP" || warn "LivenessProbe failed"\n'
    '\n'
    'hdr "13. World Article Feeder"'
)

# Extend the kill-list too
KILL_OLD = (
    'sentinel_directive_generator.py gate_scheduler.py; do'
)
KILL_NEW = (
    'sentinel_directive_generator.py gate_scheduler.py \\\n'
    '            liveness_probe.py; do'
)

# Extend the summary section
SUMMARY_OLD = (
    'echo "  GateScheduler:   '
    '$(pgrep -f \'gate_scheduler.py\' 2>/dev/null | wc -l) instance(s) [nohup, 6h cadence]"'
)
SUMMARY_NEW = (
    'echo "  GateScheduler:   '
    '$(pgrep -f \'gate_scheduler.py\' 2>/dev/null | wc -l) instance(s) [nohup, 6h cadence]"\n'
    'echo "  LivenessProbe:   '
    '$(pgrep -f \'liveness_probe.py\' 2>/dev/null | wc -l) instance(s) [wrapper, 60s poll]"'
)


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main():
    print("=" * 60)
    print("go.sh: add section 12.7 liveness_probe (commit 3.5)")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2

    src = TARGET.read_text()

    if MARKER in src:
        print("  [skip] liveness_probe already in go.sh")
        return 0

    patches = [
        ("A", "insert section 12.7",        ANCHOR_OLD, ANCHOR_NEW),
        ("B", "kill-list adds liveness_probe", KILL_OLD,  KILL_NEW),
        ("C", "summary adds LivenessProbe", SUMMARY_OLD, SUMMARY_NEW),
    ]

    for label, desc, old, new in patches:
        if old not in src:
            print(f"  [FAIL {label}] {desc}: anchor not found verbatim")
            return 2
        src = src.replace(old, new, 1)
        print(f"  [patch {label}] {desc}")

    # Validate bash
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
    print("\nDeploy:")
    print("  cd /home/workspace/zo_mesh && bash go.sh")
    print("\nVerify liveness_probe after zm go:")
    print("  pgrep -f liveness_probe.py   # expect 1 PID")
    print("  tail -f /home/workspace/logs/liveness_probe.log")
    return 0


if __name__ == "__main__":
    sys.exit(main())