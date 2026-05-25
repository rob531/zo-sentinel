#!/usr/bin/env python3
"""
patch_go_sh_add_signal_bridge.py  -- commit 4.1 completion

Add section 12.8 to go.sh that launches signal_bridge.py under the
daemon_wrapper. Must run AFTER:
  - patch_go_sh_adopt_daemon_wrapper.py (provides daemon_wrapper.sh)
  - patch_go_sh_add_liveness_probe.py   (adds section 12.7; this
    patcher anchors on the line AFTER 12.7)

Why signal_bridge is run via wrapper: same reason as builder and
directive_generator. If write_service dies mid-cycle, signal_bridge
crashes. Wrapper respawns it.

Idempotent. Syntax-checks bash before writing.
"""
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_mesh/go.sh")
MARKER = "12.8 Signal Bridge"

# Anchor on the "hdr '13. World Article Feeder'" line. If liveness_probe
# patcher ran first, that line sits after the 12.7 block. Either way,
# insert 12.8 BEFORE that line.

ANCHOR_OLD = 'hdr "13. World Article Feeder"'
ANCHOR_NEW = (
    'hdr "12.8 Signal Bridge (5min polling, wrapper-managed)"\n'
    '# Bridges mcp_signal_enrichments -> mcp_signal_scores.\n'
    '# Enables enrichment module diversity to reach the trust verdict path,\n'
    '# overriding signal_analyser flat defaults where appropriate.\n'
    '# Depends on write_service (section 3) and enrichment daemons (not yet\n'
    '# scheduled in go.sh; run as one-shots via builder directives).\n'
    'nohup bash $MESH/daemon_wrapper.sh signal_bridge \\\n'
    '    $SENTINEL/signal_bridge.py \\\n'
    '    >> $LOGS/signal_bridge.log 2>&1 &\n'
    'sleep 2\n'
    'SB=$(pgrep -f \'signal_bridge.py\' 2>/dev/null | head -1)\n'
    '[[ -n "$SB" ]] && ok "SignalBridge PID $SB" || warn "SignalBridge failed"\n'
    '\n'
    'hdr "13. World Article Feeder"'
)

# Extend the kill-list to include signal_bridge too
KILL_OLD = 'liveness_probe.py; do'
KILL_NEW = 'liveness_probe.py signal_bridge.py; do'
# If liveness_probe patcher NOT run, fallback anchor:
KILL_FALLBACK_OLD = 'sentinel_directive_generator.py gate_scheduler.py; do'
KILL_FALLBACK_NEW = 'sentinel_directive_generator.py gate_scheduler.py signal_bridge.py; do'

# Extend the summary section
SUMMARY_ANCHOR_OLD_WITH_LP = (
    'echo "  LivenessProbe:   '
    '$(pgrep -f \'liveness_probe.py\' 2>/dev/null | wc -l) instance(s) [wrapper, 60s poll]"'
)
SUMMARY_ANCHOR_NEW_WITH_LP = (
    'echo "  LivenessProbe:   '
    '$(pgrep -f \'liveness_probe.py\' 2>/dev/null | wc -l) instance(s) [wrapper, 60s poll]"\n'
    'echo "  SignalBridge:    '
    '$(pgrep -f \'signal_bridge.py\' 2>/dev/null | wc -l) instance(s) [wrapper, 5min poll]"'
)
# Fallback if liveness_probe isn't in summary yet
SUMMARY_FALLBACK_OLD = (
    'echo "  GateScheduler:   '
    '$(pgrep -f \'gate_scheduler.py\' 2>/dev/null | wc -l) instance(s) [nohup, 6h cadence]"'
)
SUMMARY_FALLBACK_NEW = (
    'echo "  GateScheduler:   '
    '$(pgrep -f \'gate_scheduler.py\' 2>/dev/null | wc -l) instance(s) [nohup, 6h cadence]"\n'
    'echo "  SignalBridge:    '
    '$(pgrep -f \'signal_bridge.py\' 2>/dev/null | wc -l) instance(s) [wrapper, 5min poll]"'
)


def _backup(path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main():
    print("=" * 60)
    print("go.sh: add section 12.8 signal_bridge (commit 4.1 completion)")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2

    src = TARGET.read_text()

    if MARKER in src:
        print("  [skip] signal_bridge already in go.sh")
        return 0

    # Patch 1: section 12.8 insertion
    if ANCHOR_OLD not in src:
        print("  [FAIL] section-13 anchor missing; cannot insert 12.8")
        return 2
    src = src.replace(ANCHOR_OLD, ANCHOR_NEW, 1)
    print("  [patch A] section 12.8 inserted")

    # Patch 2: kill-list (try primary anchor, fall back)
    if KILL_OLD in src:
        src = src.replace(KILL_OLD, KILL_NEW, 1)
        print("  [patch B] kill-list extended (liveness_probe present)")
    elif KILL_FALLBACK_OLD in src:
        src = src.replace(KILL_FALLBACK_OLD, KILL_FALLBACK_NEW, 1)
        print("  [patch B] kill-list extended (fallback anchor)")
    else:
        print("  [FAIL] neither kill-list anchor found")
        return 2

    # Patch 3: summary (try with-LP anchor first, fall back)
    if SUMMARY_ANCHOR_OLD_WITH_LP in src:
        src = src.replace(
            SUMMARY_ANCHOR_OLD_WITH_LP, SUMMARY_ANCHOR_NEW_WITH_LP, 1
        )
        print("  [patch C] summary extended after LivenessProbe")
    elif SUMMARY_FALLBACK_OLD in src:
        src = src.replace(SUMMARY_FALLBACK_OLD, SUMMARY_FALLBACK_NEW, 1)
        print("  [patch C] summary extended after GateScheduler (fallback)")
    else:
        print("  [FAIL] neither summary anchor found")
        return 2

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
    print("\nVerify:")
    print("  pgrep -f signal_bridge.py   # expect 1 PID")
    print("  tail -f /home/workspace/logs/signal_bridge.log")
    print("  # After ~5min first cycle, check DB:")
    print("  # expect supply_chain and temporal_stability distinct_vals to climb")
    return 0


if __name__ == "__main__":
    sys.exit(main())