#!/usr/bin/env bash
# install_gate_cron.sh
# ----------------------------------------------------------------------------
# Install a cron entry that runs the gate orchestrator every 6 hours.
#
# Matches the go.sh v1.3 pattern: self-reinstalls cleanly on every invocation,
# so `zm go` (or manual re-run) always leaves the cron in a known-good state.
# Idempotent -- running twice does not create duplicate entries.
#
# Schedule:   0 */6 * * *   (00:00, 06:00, 12:00, 18:00 UTC)
# Command:    python3 /home/workspace/zo_sentinel/tests/gates/run_gates_periodic.py
# Log:        /home/workspace/logs/gate_cron.log  (cron wrapper's own log)
#
# Gate output itself lands in /home/workspace/logs/gate_runs/gate_run_<ts>.log
# Individual runs are additionally memorialized in gate_errors.db.
#
# Why cron not supervisord:
#   Gates are one-shot jobs, not daemons. supervisord wants long-running
#   processes. Cron is the right shape for "fire every 6h then die".
#
# Why self-reinstall:
#   ZoComputer reboots wipe crontabs. `zm go` invokes this script to keep
#   the sentinel pipeline healthy; this script ensures the cron is also
#   always in place.
# ----------------------------------------------------------------------------
set -uo pipefail

SCRIPT=/home/workspace/zo_sentinel/tests/gates/run_gates_periodic.py
LOG=/home/workspace/logs/gate_cron.log
SCHEDULE="0 */6 * * *"
MARKER="# zo_sentinel_gates"

GRN=$'\033[0;32m'; YLW=$'\033[0;33m'; RED=$'\033[0;31m'; NC=$'\033[0m'
ok()   { printf "  %s[OK]%s %s\n" "$GRN" "$NC" "$*"; }
warn() { printf "  %s[!]%s %s\n"  "$YLW" "$NC" "$*"; }
bad()  { printf "  %s[X]%s %s\n"  "$RED" "$NC" "$*"; }

if [[ ! -f "$SCRIPT" ]]; then
    bad "$SCRIPT missing -- deploy run_gates_periodic.py first"
    exit 2
fi

# Python syntax validation on the script we're about to cron
if ! python3 -c "import ast; ast.parse(open('$SCRIPT').read())" 2>/dev/null; then
    bad "$SCRIPT has syntax errors -- refusing to install cron"
    exit 2
fi

mkdir -p /home/workspace/logs

CURRENT=$(crontab -l 2>/dev/null || true)

# Filter out any existing zo_sentinel_gates lines (idempotent)
CLEAN=$(echo "$CURRENT" | grep -v "$MARKER" || true)

NEW_ENTRY="$SCHEDULE python3 $SCRIPT >> $LOG 2>&1 $MARKER"

# Reassemble crontab
if [[ -z "$CLEAN" ]]; then
    UPDATED="$NEW_ENTRY"
else
    UPDATED="$CLEAN"$'\n'"$NEW_ENTRY"
fi

echo "$UPDATED" | crontab -

if crontab -l 2>/dev/null | grep -qF "$MARKER"; then
    ok "cron installed: $SCHEDULE"
    ok "  script:   $SCRIPT"
    ok "  log:      $LOG"
    ok "  gate runs: /home/workspace/logs/gate_runs/gate_run_<ts>.log"
else
    bad "crontab update failed"
    exit 2
fi

# Sanity: list all zo_* cron entries so user sees the full picture
echo
warn "Current crontab (zo entries):"
crontab -l 2>/dev/null | grep -E '(zo_|sentinel|gate)' || echo "  (none besides this one)"

echo
ok "Done. Next run will fire at the next 0/6/12/18 UTC hour."
ok "Force-run for testing:  python3 $SCRIPT"