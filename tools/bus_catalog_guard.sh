#!/usr/bin/env bash
# Self-healing wrapper for tools/bus_catalog_refresh.sh.
#
# WHY THIS FILE EXISTS
#   bus_catalog_refresh.sh ran on ONE crontab line, `17 5 * * *`. On 2026-08-26
#   the box was powered off at 05:17 and booted at 08:38, so the slot was simply
#   missed. Nothing retried it and nothing reported it. There was no impact that
#   day -- the snapshot was 0.61d old against a 14d budget -- but a routinely
#   missed slot ages the snapshot until referent-verify turns UNKNOWN-red, and
#   the failure would then be read as "the checker broke" rather than "the box
#   was off at 05:17 for a fortnight".
#
#   A daily timer is the wrong shape for a machine that is not always on. This
#   makes the refresh idempotent and event-driven instead:
#
#     on boot      -- catch a slot missed while powered off
#     on schedule  -- the ordinary daily path (unchanged)
#     on demand    -- an hourly cheap check that acts ONLY if the snapshot is
#                     older than STALE_HOURS
#
#   All three call this script; it decides whether work is needed. Running it
#   more often costs one file stat when the snapshot is fresh.
#
# VISIBILITY
#   Every run -- including "nothing to do" -- stamps a heartbeat with the
#   snapshot age. A missed run is then visible as a heartbeat that stopped
#   moving, rather than only as a red gate a fortnight later. The heartbeat is
#   deliberately written on the no-op path too: a heartbeat that only appears
#   when work happens cannot distinguish "healthy and idle" from "dead".
#
# Usage:  tools/bus_catalog_guard.sh [--force] [--dry-run]
set -uo pipefail

REPO="${ZO_REPO:-/home/workspace/zo_sentinel}"
# The snapshot is read from origin/main, NOT from the working tree.
# /home/workspace/zo_sentinel runs ~130 commits behind main and does not even
# contain schema/bus_catalog.json -- reading the working tree reported the
# snapshot as MISSING and triggered a refresh on every single run. That is
# audit finding B2 in miniature: a path into the build workspace is not a
# reliable reference to the reviewed state.
SNAP_REF="${ZO_CATALOG_SNAP_REF:-origin/main:schema/bus_catalog.json}"
STALE_HOURS="${STALE_HOURS:-26}"      # just over a day: one missed daily slot
HEARTBEAT="${ZO_CATALOG_HEARTBEAT:-/home/workspace/logs/bus_catalog_heartbeat.json}"
BOOT_SETTLE="${BOOT_SETTLE:-0}"       # seconds to wait for the bus after @reboot
FORCE=0; DRY=""
for a in "$@"; do
  case "$a" in
    --force)   FORCE=1 ;;
    --dry-run) DRY="--dry-run" ;;
  esac
done

log() { echo "[catalog-guard] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }

[ "$BOOT_SETTLE" -gt 0 ] && { log "boot settle ${BOOT_SETTLE}s"; sleep "$BOOT_SETTLE"; }

# --- how old is the committed snapshot? ------------------------------------
# captured_at is the field referent_verify.py measures its budget against, so
# it is the field this must measure too. Reading mtime instead would call a
# freshly-checked-out stale file "new".
age_hours() {
  # Materialise the TRACKED snapshot to a temp file, then measure it.
  local tmp; tmp="$(mktemp)"
  if ! git -C "$REPO" show "$SNAP_REF" > "$tmp" 2>/dev/null; then
    rm -f "$tmp"; echo "-1"; return
  fi
  python3 - "$tmp" <<'PY'
import json, sys
from datetime import datetime, timezone
try:
    d = json.load(open(sys.argv[1]))
    ts = datetime.fromisoformat(d["captured_at"])
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    print(f"{(datetime.now(timezone.utc) - ts).total_seconds()/3600:.2f}")
except Exception:
    print("-1")            # unreadable/missing -> treat as stale, never as fresh
PY
  rm -f "$tmp"
}

git -C "$REPO" fetch -q origin main 2>/dev/null || log "WARN: could not fetch origin/main; measuring against the last known ref"
AGE="$(age_hours)"
log "snapshot age ${AGE}h (stale threshold ${STALE_HOURS}h, referent-verify budget 336h)"

NEED=0
REASON="fresh"
if [ "$FORCE" = "1" ]; then NEED=1; REASON="--force"
elif [ "$AGE" = "-1" ]; then NEED=1; REASON="snapshot missing or unreadable"
else
  # bash has no float compare; let python decide.
  if python3 -c "import sys;sys.exit(0 if float('$AGE') > float('$STALE_HOURS') else 1)"; then
    NEED=1; REASON="older than ${STALE_HOURS}h"
  fi
fi

RC=0
if [ "$NEED" = "1" ]; then
  log "refresh NEEDED ($REASON) -> running bus_catalog_refresh.sh"
  # Run the script AS TRACKED ON main, same discipline as the crontab line:
  # the build workspace runs behind main and carries untracked files (B2), so a
  # path into it is not a reliable reference to the reviewed code.
  if git -C "$REPO" fetch -q origin main \
     && git -C "$REPO" show origin/main:tools/bus_catalog_refresh.sh > /tmp/bus_catalog_refresh.sh; then
    bash /tmp/bus_catalog_refresh.sh $DRY; RC=$?
    log "bus_catalog_refresh.sh exited $RC"
  else
    RC=90; log "could not fetch the tracked refresh script from origin/main"
  fi
else
  log "no action ($REASON)"
fi

# --- heartbeat, written on EVERY path including the no-op ------------------
mkdir -p "$(dirname "$HEARTBEAT")" 2>/dev/null
python3 - "$HEARTBEAT" "$AGE" "$NEED" "$REASON" "$RC" <<'PY'
import json, sys
from datetime import datetime, timezone
path, age, need, reason, rc = sys.argv[1:6]
json.dump({
    "last_run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "snapshot_age_hours": float(age),
    "action_taken": need == "1",
    "reason": reason,
    "refresh_exit_code": int(rc),
}, open(path, "w"), indent=2)
PY
log "heartbeat -> $HEARTBEAT"
exit "$RC"
