#!/usr/bin/env python3
"""
patch_go_sh_add_directive_generator.py

Integrate sentinel_directive_generator into the zm go recovery script so
Layer 1 directive generation persists across ZoComputer reboots.

Two surgical edits to /home/workspace/zo_mesh/go.sh:

  1. Kill-list (section 1): add sentinel_directive_generator.py so old
     instances are killed before the new one starts. Without this, a zm go
     during an active session would leave a stale process alongside the
     new one, both heartbeating under the same name.

  2. New section 12.5: start sentinel_directive_generator via nohup, same
     pattern as the builder. Inserted AFTER the builder (section 12) and
     BEFORE the world article feeder (section 13). Rationale: builder is
     the consumer of the directives, so generator should start once builder
     is already listening.

Idempotent. Uses plain string anchors (no regex). Pre-flight syntax check
is bash -n (not Python ast), called externally if desired. We just ensure
the resulting file is well-formed by verifying the anchors exist before
applying.

Run with:
  python3 /home/workspace/zo_sentinel/fixes/patch_go_sh_add_directive_generator.py
"""
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

TARGET = Path("/home/workspace/zo_mesh/go.sh")

# ---- Patch 1: kill-list addition ------------------------------------------

KILL_LIST_OLD = """for proc in write_service.py inference_router_service.py run_manager.py \\
            pipeline_bridge.py t2_consumer_agents.py anti_entropy_daemon.py \\
            mesh_self_diagnostics.py data_velocity_engine.py wisdom_synthesiser.py \\
            world_article_feeder.py zo_sentinel_builder.py; do"""

KILL_LIST_NEW = """for proc in write_service.py inference_router_service.py run_manager.py \\
            pipeline_bridge.py t2_consumer_agents.py anti_entropy_daemon.py \\
            mesh_self_diagnostics.py data_velocity_engine.py wisdom_synthesiser.py \\
            world_article_feeder.py zo_sentinel_builder.py \\
            sentinel_directive_generator.py; do"""

# ---- Patch 2: new section between 12 and 13 -------------------------------

SECTION_12_END_OLD = """    warn "SentinelBuilder failed to start"
fi

hdr "13. World Article Feeder\""""

SECTION_12_END_NEW = """    warn "SentinelBuilder failed to start"
fi

hdr "12.5 ZO-SENTINEL Directive Generator (Layer 1 enriched)"
# Reads PRODUCT_SPEC.md + live wiring/gaps maps to generate grounded directives.
# Polls every 2h (7200s). Skips when queue >=5 directives already pending.
# Dependency: write_service must be up (section 3) for the heartbeat / mesh_memory writes.
nohup python3 $SENTINEL/sentinel_directive_generator.py >> $LOGS/sentinel_sentinel_directive_generator.log 2>&1 &
sleep 2
SDG=$(pgrep -f 'sentinel_directive_generator.py' 2>/dev/null | head -1)
[[ -n "$SDG" ]] && ok "DirectiveGenerator PID $SDG (MINIMAX: $([[ -n \"$MINIMAX_API_KEY\" ]] && echo SET || echo NOT_SET))" || warn "DirectiveGenerator failed to start"

hdr "13. World Article Feeder\""""

# ---- Patch 3: summary line ------------------------------------------------
# Add a DirectiveGenerator instance count to the SUMMARY block so you can
# tell at a glance if it's running.

SUMMARY_OLD = """echo "  SentinelBuilder: $(pgrep -f 'zo_sentinel_builder.py' 2>/dev/null | wc -l) instance(s) [nohup]\""""

SUMMARY_NEW = """echo "  SentinelBuilder: $(pgrep -f 'zo_sentinel_builder.py' 2>/dev/null | wc -l) instance(s) [nohup]"
echo "  DirectiveGen:    $(pgrep -f 'sentinel_directive_generator.py' 2>/dev/null | wc -l) instance(s) [nohup]\""""


def _backup(path: Path):
    ts = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    bak = path.with_suffix(path.suffix + f".bak.{ts}")
    shutil.copy2(path, bak)
    print(f"  [backup] {bak.name}")


def main() -> int:
    print("=" * 60)
    print("zm go: integrate sentinel_directive_generator")
    print("=" * 60)

    if not TARGET.exists():
        print(f"  [FAIL] target not found: {TARGET}")
        return 2
    src = TARGET.read_text()
    changed = False

    # Patch 1: kill-list
    if "sentinel_directive_generator.py;" in src or "sentinel_directive_generator.py; do" in src:
        print("  [skip 1] sentinel_directive_generator.py already in kill-list")
    elif KILL_LIST_OLD in src:
        src = src.replace(KILL_LIST_OLD, KILL_LIST_NEW, 1)
        print("  [patch 1] added sentinel_directive_generator.py to kill-list")
        changed = True
    else:
        print("  [FAIL 1] kill-list anchor not found verbatim")
        print("           expected this anchor:")
        print("           " + KILL_LIST_OLD[:120].replace(chr(10), " | "))
        return 2

    # Patch 2: new section
    if 'hdr "12.5 ZO-SENTINEL Directive Generator' in src:
        print("  [skip 2] section 12.5 already present")
    elif SECTION_12_END_OLD in src:
        src = src.replace(SECTION_12_END_OLD, SECTION_12_END_NEW, 1)
        print("  [patch 2] inserted section 12.5 (DirectiveGenerator launch)")
        changed = True
    else:
        print("  [FAIL 2] section 12 end anchor not found verbatim")
        return 2

    # Patch 3: summary line
    if "DirectiveGen:" in src:
        print("  [skip 3] summary line already present")
    elif SUMMARY_OLD in src:
        src = src.replace(SUMMARY_OLD, SUMMARY_NEW, 1)
        print("  [patch 3] added DirectiveGen line to SUMMARY")
        changed = True
    else:
        print("  [FAIL 3] summary anchor not found verbatim")
        return 2

    if not changed:
        print("\n  [noop] all patches already applied")
        return 0

    _backup(TARGET)
    TARGET.write_text(src)
    print(f"\n  [done] {TARGET.name} patched")
    print("\nVerify with a dry-read:")
    print("  grep -n 'sentinel_directive_generator' /home/workspace/zo_mesh/go.sh")
    print("\nSyntax-check with bash -n (does not execute):")
    print("  bash -n /home/workspace/zo_mesh/go.sh && echo 'bash syntax OK'")
    print("\nThen next zm go will include DirectiveGenerator. No restart needed now")
    print("because the current instance is already running. But after any container")
    print("reboot, zm go will bring it back automatically.")
    return 0


if __name__ == "__main__":
    sys.exit(main())