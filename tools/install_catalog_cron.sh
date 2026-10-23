#!/usr/bin/env bash
# install_catalog_cron.sh -- (re)install the self-healing bus-catalog refresh.
#
# WHY AN INSTALLER AND NOT JUST A CRONTAB LINE
#   install_gate_cron.sh states the fact that decides this file's shape:
#   "ZoComputer reboots wipe crontabs." A hand-edited crontab is therefore not
#   a durable registration -- it is a registration that survives until the next
#   reboot, which is exactly the event it is supposed to protect against.
#
#   So the installation is tracked, idempotent and re-runnable, matching the
#   install_gate_cron.sh pattern: marker-tagged lines, filtered and rewritten on
#   every invocation, safe to run twice.
#
# WHAT IT INSTALLS -- three triggers, one idempotent guard
#   The refresh ran on ONE slot, `17 5 * * *`. On 2026-08-26 the box was off at
#   05:17 and booted at 08:38, so the slot was missed; nothing retried it and
#   nothing reported it. A daily timer is the wrong shape for a machine that is
#   not always on.
#
#     @reboot   catch a slot missed while powered off (60s settle for the bus)
#     hourly    act ONLY if the tracked snapshot is older than STALE_HOURS
#     05:17     the ordinary daily path, kept as the steady heartbeat
#
#   All three call tools/bus_catalog_guard.sh, which decides whether work is
#   actually needed. When the snapshot is fresh the hourly run costs one
#   `git show` and exits.
#
#   Each line fetches the guard AS TRACKED ON main and runs that -- never a path
#   into the build workspace, which runs behind main and carries untracked files
#   (audit finding B2). git -C, not cd: cron starts in $HOME, not a repo.
#
# VISIBILITY
#   Every run, including "nothing to do", stamps
#   /home/workspace/logs/bus_catalog_heartbeat.json with the snapshot age. A
#   missed run is then visible as a heartbeat that stopped moving, rather than
#   only as a red gate once the 14-day budget finally expires.
#
# Usage:  tools/install_catalog_cron.sh [--dry-run]
set -uo pipefail

REPO="${ZO_REPO:-/home/workspace/zo_sentinel}"
LOG="${ZO_CATALOG_LOG:-/home/workspace/logs/bus_catalog_refresh.log}"
MARKER="# zo_catalog_refresh"
DRY=0
[ "${1:-}" = "--dry-run" ] && DRY=1

GRN=$'\033[0;32m'; YLW=$'\033[0;33m'; RED=$'\033[0;31m'; NC=$'\033[0m'
ok()   { printf "  %s[OK]%s %s\n" "$GRN" "$NC" "$*"; }
warn() { printf "  %s[!]%s %s\n"  "$YLW" "$NC" "$*"; }
bad()  { printf "  %s[X]%s %s\n"  "$RED" "$NC" "$*"; }

# Refuse to install a cron for a guard that is not on main -- the cron lines
# fetch it from origin/main, so a guard that only exists locally installs three
# lines that can never run.
if ! git -C "$REPO" fetch -q origin main 2>/dev/null; then
    warn "could not fetch origin/main; validating against the last known ref"
fi
if ! git -C "$REPO" show origin/main:tools/bus_catalog_guard.sh > /tmp/_guard_check.sh 2>/dev/null; then
    bad "tools/bus_catalog_guard.sh is not on origin/main -- refusing to install"
    bad "  the cron lines fetch it from there; merge it first"
    rm -f /tmp/_guard_check.sh
    exit 2
fi
if ! bash -n /tmp/_guard_check.sh 2>/dev/null; then
    bad "the guard on origin/main has shell syntax errors -- refusing to install"
    rm -f /tmp/_guard_check.sh
    exit 2
fi
rm -f /tmp/_guard_check.sh
ok "guard validated on origin/main"

mkdir -p "$(dirname "$LOG")" 2>/dev/null

FETCH="git -C $REPO fetch -q origin main && git -C $REPO show origin/main:tools/bus_catalog_guard.sh > /tmp/bus_catalog_guard.sh"

L1="@reboot BOOT_SETTLE=60 $FETCH && BOOT_SETTLE=60 bash /tmp/bus_catalog_guard.sh >> $LOG 2>&1 $MARKER"
L2="7 * * * * $FETCH && bash /tmp/bus_catalog_guard.sh >> $LOG 2>&1 $MARKER"
L3="17 5 * * * $FETCH && bash /tmp/bus_catalog_guard.sh >> $LOG 2>&1 $MARKER"

CURRENT="$(crontab -l 2>/dev/null || true)"
# Idempotent: drop our own marked lines AND the original unmarked 05:17 line
# this supersedes, so re-running never accumulates duplicates.
CLEAN="$(printf '%s\n' "$CURRENT" \
         | grep -vF "$MARKER" \
         | grep -v 'bus_catalog_guard.sh' \
         | grep -v 'bus_catalog_refresh.sh >> /home/workspace/logs/bus_catalog_refresh.log' \
         || true)"

UPDATED="$CLEAN"
for L in "$L1" "$L2" "$L3"; do
    UPDATED="$UPDATED"$'\n'"$L"
done

if [ "$DRY" = "1" ]; then
    warn "DRY RUN -- would install:"
    printf '%s\n' "$L1" "$L2" "$L3"
    exit 0
fi

printf '%s\n' "$UPDATED" | crontab -

N="$(crontab -l 2>/dev/null | grep -cF "$MARKER" || true)"
if [ "$N" = "3" ]; then
    ok "installed 3 marked entries (@reboot, hourly, 05:17)"
    ok "  guard: origin/main:tools/bus_catalog_guard.sh"
    ok "  log:   $LOG"
    ok "  heartbeat: /home/workspace/logs/bus_catalog_heartbeat.json"
    exit 0
fi
bad "expected 3 marked entries, found $N"
exit 1
