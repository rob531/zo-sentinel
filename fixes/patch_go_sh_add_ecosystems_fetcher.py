#!/usr/bin/env python3
"""
patch_go_sh_add_ecosystems_fetcher.py  -- Commit A deploy

Adds section 12.9 to go.sh to launch ecosystems_metadata_fetcher under
daemon_wrapper. Follows the same pattern as signal_bridge (section 12.8).

Does NOT add ecosystems_enrichment_adapter to go.sh -- that one runs in
--once mode, invoked by the operator (or eventually by a cron/scheduler)
after the fetcher has warmed its cache. Running the adapter BEFORE the
fetcher has data would write useless zero-download scores everywhere.

Recommended operator flow after deploy:
  1. Apply this patcher, run 'zm go'
  2. Let fetcher run 1 cycle (populates ~50 servers)
  3. Run adapter once: python3 /home/workspace/zo_sentinel/ecosystems_enrichment_adapter.py --once
  4. Check mcp_signal_enrichments for new community_signal_enrichment rows
  5. Within 5 minutes signal_bridge picks them up and updates mcp_signal_scores
  6. Gate 9 on next run shows improved discrimination

Idempotent. bash -n validated.
"""
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_mesh/go.sh")
MARKER = "12.9 Ecosystems Metadata Fetcher"

ANCHOR_OLD = 'hdr "13. World Article Feeder"'
ANCHOR_NEW = (
    'hdr "12.9 Ecosystems Metadata Fetcher (6h cycle, wrapper-managed)"\n'
    '# Fetches cross-registry metadata from packages.ecosyste.ms for each MCP.\n'
    '# Populates mcp_ecosystems_metadata table with downloads, age, ecosystems.\n'
    '# Feeds community_signal + temporal_stability enrichment via the\n'
    '# ecosystems_enrichment_adapter (run separately, not as a daemon today).\n'
    '# Cache TTL 24h; batch 50/cycle; full sweep of 790 MCPs takes ~24h.\n'
    '# Rate limit on ecosyste.ms: 5000/hr; we use ~200/hr max.\n'
    'nohup bash $MESH/daemon_wrapper.sh ecosystems_metadata_fetcher \\\n'
    '    $SENTINEL/ecosystems_metadata_fetcher.py \\\n'
    '    >> $LOGS/ecosystems_metadata_fetcher.log 2>&1 &\n'
    'sleep 2\n'
    'EMF=$(pgrep -f \'ecosystems_metadata_fetcher.py\' 2>/dev/null | head -1)\n'
    '[[ -n "$EMF" ]] && ok "EcosystemsFetcher PID $EMF" || warn "EcosystemsFetcher failed"\n'
    '\n'
    'hdr "13. World Article Feeder"'
)

# Extend the kill-list. Try multiple anchors in case prior patchers
# have/have not run.
KILL_ANCHORS = [
    ("liveness_probe.py signal_bridge.py; do",
     "liveness_probe.py signal_bridge.py ecosystems_metadata_fetcher.py; do"),
    ("liveness_probe.py; do",
     "liveness_probe.py ecosystems_metadata_fetcher.py; do"),
    ("sentinel_directive_generator.py gate_scheduler.py; do",
     "sentinel_directive_generator.py gate_scheduler.py ecosystems_metadata_fetcher.py; do"),
]

# Extend SUMMARY. Anchor on SignalBridge (added in 12.8 patcher)
# with a fallback to GateScheduler line if SignalBridge isn't there.
SUMMARY_ANCHORS = [
    (
        'echo "  SignalBridge:    $(pgrep -f \'signal_bridge.py\' 2>/dev/null | wc -l) instance(s) [wrapper, 5min poll]"',
        'echo "  SignalBridge:    $(pgrep -f \'signal_bridge.py\' 2>/dev/null | wc -l) instance(s) [wrapper, 5min poll]"\n'
        'echo "  EcosystemsFet:   $(pgrep -f \'ecosystems_metadata_fetcher.py\' 2>/dev/null | wc -l) instance(s) [wrapper, 6h cycle]"',
    ),
    (
        'echo "  LivenessProbe:   $(pgrep -f \'liveness_probe.py\' 2>/dev/null | wc -l) instance(s) [wrapper, 60s poll]"',
        'echo "  LivenessProbe:   $(pgrep -f \'liveness_probe.py\' 2>/dev/null | wc -l) instance(s) [wrapper, 60s poll]"\n'
        'echo "  EcosystemsFet:   $(pgrep -f \'ecosystems_metadata_fetcher.py\' 2>/dev/null | wc -l) instance(s) [wrapper, 6h cycle]"',
    ),
]


def _backup(path):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def _try_replace(src, anchors, label):
    for old, new in anchors:
        if old in src:
            return src.replace(old, new, 1), label + " applied"
    return None, label + " NO ANCHOR MATCHED"


def main():
    print("=" * 60)
    print("go.sh: add section 12.9 ecosystems_metadata_fetcher (commit A)")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2

    src = TARGET.read_text()
    if MARKER in src:
        print("  [skip] section 12.9 already present")
        return 0

    if ANCHOR_OLD not in src:
        print("  [FAIL] section-13 anchor missing; cannot insert 12.9")
        return 2
    src = src.replace(ANCHOR_OLD, ANCHOR_NEW, 1)
    print("  [patch A] section 12.9 inserted")

    # Kill-list
    new_src, msg = _try_replace(src, KILL_ANCHORS, "kill-list")
    if new_src is None:
        print(f"  [FAIL] {msg}")
        return 2
    src = new_src
    print(f"  [patch B] {msg}")

    # Summary
    new_src, msg = _try_replace(src, SUMMARY_ANCHORS, "summary")
    if new_src is None:
        print(f"  [WARN] {msg} -- summary not updated but section 12.9 still inserted")
    else:
        src = new_src
        print(f"  [patch C] {msg}")

    # bash -n
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
    print("\nDeploy sequence:")
    print("  cd /home/workspace/zo_mesh && bash go.sh")
    print("  sleep 90   # wait for first fetcher cycle to populate ~50 servers")
    print("  python3 /home/workspace/zo_sentinel/ecosystems_enrichment_adapter.py --once")
    print("\nVerify first cycle populated data:")
    print("  # Check mcp_ecosystems_metadata row count climbs over time:")
    print("  curl -s http://127.0.0.1:8772/query -H 'Content-Type: application/json' \\")
    print("    -d '{\"sql\":\"SELECT lookup_status, COUNT(*) FROM mcp_ecosystems_metadata GROUP BY 1\"}'")
    print("\n  # Check community signals now have real download data:")
    print("  curl -s http://127.0.0.1:8772/query -H 'Content-Type: application/json' \\")
    print("    -d '{\"sql\":\"SELECT signal_name, COUNT(DISTINCT score) FROM mcp_signal_scores GROUP BY 1\"}'")
    print("\n  # Run Gate 9 to see improved discrimination on community_signal:")
    print("  python3 /home/workspace/zo_sentinel/tests/gates/run_gates.py 9")
    return 0


if __name__ == "__main__":
    sys.exit(main())