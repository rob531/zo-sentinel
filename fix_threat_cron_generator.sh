#!/usr/bin/env bash
# fix_threat_cron_generator.sh
# ----------------------------------------------------------------------------
# Three small fixes identified 2026-04-17 18:56 UTC:
#
#   1. Restart threat_intel_ingestor -- running process has pre-patch code
#      still pointing at :8773/execute. Disk file is correct, process needs
#      to reload. Same check for rug_pull_monitor and attestation_engine
#      which may be in the same state.
#
#   2. Re-install gate cron -- previously believed installed but no evidence
#      in crontab or gate_cron.log. Could be reboot wipe, could be silent
#      install failure.
#
#   3. Kick directive generator into producing new directives -- its last
#      cycle (17:38 UTC) failed MiniMax JSON parse; next natural cycle is
#      19:38 UTC. Kicking it now avoids wasting more idle builder time.
#
# Each step is independent. If one fails, the others still run.
# ----------------------------------------------------------------------------
set -uo pipefail

SENTINEL=/home/workspace/zo_sentinel
LOGS=/home/workspace/logs

GRN=$'\033[0;32m'; YLW=$'\033[0;33m'; RED=$'\033[0;31m'; NC=$'\033[0m'
ok()   { printf "  %s[OK]%s %s\n" "$GRN" "$NC" "$*"; }
warn() { printf "  %s[!]%s %s\n"  "$YLW" "$NC" "$*"; }
bad()  { printf "  %s[X]%s %s\n"  "$RED" "$NC" "$*"; }
hdr()  { printf "\n=== %s ===\n" "$*"; }

###############################################################################
hdr "Step 1: restart daemons holding stale in-memory code"
###############################################################################

# Daemons that were patched on disk but may still be running pre-patch code
for name in threat_intel_ingestor rug_pull_monitor attestation_engine; do
    script="$name.py"
    pid="$(pgrep -f "python3 .*$script" 2>/dev/null | head -1)"
    if [[ -z "$pid" ]]; then
        warn "$name not running (nothing to restart)"
        # Start it fresh
        nohup python3 "$SENTINEL/$script" >> "$LOGS/sentinel_$name.log" 2>&1 &
        sleep 2
        new_pid="$(pgrep -f "python3 .*$script" 2>/dev/null | head -1)"
        if [[ -n "$new_pid" ]]; then
            ok "$name started fresh (PID $new_pid)"
        else
            bad "$name failed to start -- check $LOGS/sentinel_$name.log"
        fi
        continue
    fi

    # Check whether disk has the post-patch port. If yes AND process is old,
    # restart. If disk still has :8773, something's broken upstream.
    if grep -qF "8773/execute" "$SENTINEL/$script"; then
        bad "$name: disk still has :8773/execute -- port patcher didn't apply!"
        bad "   re-run: bash /home/workspace/zo_sentinel/patch_sentinel_port_8773_to_8772.sh"
        continue
    fi

    warn "$name PID $pid was running before patch; restarting to reload code"
    kill -9 "$pid" 2>/dev/null
    sleep 2
    nohup python3 "$SENTINEL/$script" >> "$LOGS/sentinel_$name.log" 2>&1 &
    sleep 2
    new_pid="$(pgrep -f "python3 .*$script" 2>/dev/null | head -1)"
    if [[ -n "$new_pid" ]]; then
        ok "$name restarted (old=$pid, new=$new_pid)"
    else
        bad "$name failed to restart -- check $LOGS/sentinel_$name.log"
    fi
done

###############################################################################
hdr "Step 2: verify / re-install gate cron"
###############################################################################

INSTALLER="/home/workspace/zo_sentinel/install_gate_cron.sh"
if [[ ! -f "$INSTALLER" ]]; then
    bad "$INSTALLER missing -- cannot install cron"
else
    # Show current crontab state before touching
    if crontab -l 2>/dev/null | grep -q 'zo_sentinel_gates'; then
        ok "gate cron already present in crontab"
    else
        warn "gate cron NOT in crontab -- installing now"
        bash "$INSTALLER" 2>&1 | sed 's/^/    /'
    fi

    # Confirm
    if crontab -l 2>/dev/null | grep -q 'zo_sentinel_gates'; then
        ok "verified: gate cron is registered"
        echo
        warn "Active zo-related cron entries:"
        crontab -l 2>/dev/null | grep -E '(zo|sentinel|gate)' | sed 's/^/    /'
    else
        bad "gate cron STILL not in crontab after install attempt"
    fi
fi

# Also ensure /home/workspace/logs exists (cron wrapper writes gate_cron.log here)
mkdir -p "$LOGS/gate_runs"
ok "$LOGS/gate_runs/ ready (periodic runner writes here)"

###############################################################################
hdr "Step 3: kick directive generator"
###############################################################################

# Generator's 17:38 cycle failed MiniMax JSON parse. Next natural cycle at
# 19:38 UTC (~40min). Kicking it now with SIGUSR1 won't work because the
# code doesn't have a signal handler. Simplest effective kick: restart it
# so its cycle-0 logic runs immediately.
DG_PID="$(pgrep -f 'python3 .*sentinel_directive_generator.py' 2>/dev/null | head -1)"
if [[ -n "$DG_PID" ]]; then
    warn "restarting sentinel_directive_generator (PID $DG_PID) to force immediate cycle"
    kill -9 "$DG_PID" 2>/dev/null
    sleep 2
fi
nohup python3 "$SENTINEL/sentinel_directive_generator.py" \
    >> "$LOGS/sentinel_directive_generator.log" 2>&1 &
sleep 4
new_pid="$(pgrep -f 'python3 .*sentinel_directive_generator.py' 2>/dev/null | head -1)"
if [[ -n "$new_pid" ]]; then
    ok "directive_generator restarted (PID $new_pid) -- cycle runs within 30s"
else
    bad "directive_generator failed to restart -- check its log"
fi

###############################################################################
hdr "Done"
###############################################################################

echo
ok "Monitor results:"
echo "    # threat_intel_ingestor should stop emitting 404s on :8773"
echo "    tail -f $LOGS/sentinel_threat_intel_ingestor.log"
echo
echo "    # directive_generator should produce new directives within 2 min"
echo "    tail -f $LOGS/sentinel_directive_generator.log"
echo
echo "    # cron logs go here on next 0/6/12/18 UTC boundary"
echo "    ls -la $LOGS/gate_cron.log $LOGS/gate_runs/ 2>/dev/null"
echo
ok "Ask Claude to inspect mcp_threat_associations in ~20 min:"
echo "    SELECT COUNT(*) FROM mcp_threat_associations"
echo "    -- should start growing if threat_intel_ingestor patches took"