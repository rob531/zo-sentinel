#!/usr/bin/env bash
# install_catalog_supervisord.sh -- register bus-catalog-guard as a supervisord
# [program:bus-catalog-guard] entry in /etc/zo/supervisord-user.conf.
#
# WHY THIS EXISTS -- AND WHY IT IS NOT install_catalog_cron.sh AGAIN
#   tools/install_catalog_cron.sh writes three crontab entries and they were the
#   correct design until 2026-08-27, when pgrep -x cron and pgrep -x crond both
#   returned empty on the host. cron is installed at /usr/sbin/cron; it is also
#   registered in /etc/init.d. It just never runs. The container's process
#   supervisor is supervisord (PIDs 95 + 97 from /etc/zo/supervisor.conf and
#   /etc/zo/supervisord-user.conf), started by /__substrate/entrypoint.sh, and
#   nothing in that path starts cron.
#
#   Three crontab triggers on one dead scheduler is not redundancy. It looked
#   exactly like redundancy from the crontab listing, which is why nobody checked
#   the daemon. (FU-389; see also bus-catalog-freshness.yml for the off-host alarm.)
#
# WHAT THE SUPERVISORD PROGRAM DOES
#   supervisord's temporal-context-writer uses
#     command=bash -c 'while true; do <work>; sleep N; done'
#   to implement a periodic that cannot use cron. This file uses the same shape:
#   every 3600 s (one hour) it fetches bus_catalog_guard.sh FROM origin/main and
#   runs it. The guard decides whether a refresh is actually needed (idempotent,
#   one file-stat when the snapshot is fresh). The heartbeat is stamped on every
#   path including the no-op, so a missed run appears as a heartbeat that stopped
#   moving -- not as silence until referent-verify goes STALE-RED a fortnight later.
#
# DEPLOYMENT STEPS (must be run on the host, not from CI)
#
#   1. bash tools/install_catalog_supervisord.sh          # writes the conf block
#   2. supervisorctl -c /etc/zo/supervisord-user.conf reread
#   3. supervisorctl -c /etc/zo/supervisord-user.conf update
#   4. supervisorctl -c /etc/zo/supervisord-user.conf status bus-catalog-guard
#   5. Check /home/workspace/logs/bus_catalog_heartbeat.json moved within the hour.
#
#   The CONF_FILE is regenerated from an env var at cold boot (the substrate does
#   this), so a reboot will erase the block. To survive cold boots the block should
#   be added to the upstream env var; in the meantime the on-host alarm
#   (bus-catalog-freshness.yml) turns red a week before referent-verify's budget
#   expires, giving time to re-run this script.
#
# ROLLBACK
#   Remove the [program:bus-catalog-guard] block from CONF_FILE, then:
#     supervisorctl -c /etc/zo/supervisord-user.conf reread
#     supervisorctl -c /etc/zo/supervisord-user.conf update
#
# Usage:  tools/install_catalog_supervisord.sh [--dry-run] [--self-test]
set -uo pipefail

CONF_FILE="${SUPERVISORD_USER_CONF:-/etc/zo/supervisord-user.conf}"
SUPERVISOR_CTL="${SUPERVISORCTL:-supervisorctl}"
SUPERVISOR_CONF="${SUPERVISOR_CONF_PATH:-/etc/zo/supervisord-user.conf}"
REPO="${ZO_REPO:-/home/workspace/zo_sentinel}"
LOG="${ZO_CATALOG_LOG:-/home/workspace/logs/bus_catalog_refresh.log}"
MARKER="# zo_catalog_supervisord_managed"
DRY=0
SELF_TEST=0

for a in "${@:-}"; do
  case "$a" in
    --dry-run)   DRY=1 ;;
    --self-test) SELF_TEST=1 ;;
  esac
done

GRN=$'\033[0;32m'; YLW=$'\033[0;33m'; RED=$'\033[0;31m'; NC=$'\033[0m'
ok()   { printf "  %s[OK]%s %s\n" "$GRN" "$NC" "$*"; }
warn() { printf "  %s[!]%s %s\n"  "$YLW" "$NC" "$*"; }
bad()  { printf "  %s[X]%s %s\n"  "$RED" "$NC" "$*"; >&2 echo "[X] $*"; }

# ---------------------------------------------------------------------------
# --self-test: run in a tempdir, verify idempotency and --dry-run.
# Does NOT require /etc/zo or a running supervisord -- it substitutes fake paths.
# ---------------------------------------------------------------------------
if [ "$SELF_TEST" = "1" ]; then
  TD="$(mktemp -d)"
  trap 'rm -rf "$TD"' EXIT

  # Provide a minimal fake supervisor conf so the script can write into it.
  FAKE_CONF="$TD/supervisord-user.conf"
  cat > "$FAKE_CONF" <<'FAKEEOF'
[inet_http_server]
port=127.0.0.1:29011

[supervisord]
logfile=/dev/shm/supervisord_user.log
FAKEEOF

  PASS=0; FAIL=0
  check() {
    local name="$1"; local expr="$2"
    if eval "$expr" >/dev/null 2>&1; then
      ok "PASS: $name"; PASS=$((PASS+1))
    else
      bad "FAIL: $name"; FAIL=$((FAIL+1))
    fi
  }

  # Test 1: --dry-run exits 0 without touching the conf file.
  MTIME_BEFORE=$(stat -c %Y "$FAKE_CONF" 2>/dev/null || echo "0")
  SUPERVISORD_USER_CONF="$FAKE_CONF" ZO_REPO="$TD" ZO_CATALOG_LOG="$TD/refresh.log" \
    bash "$0" --dry-run >/dev/null 2>&1
  MTIME_AFTER=$(stat -c %Y "$FAKE_CONF" 2>/dev/null || echo "0")
  check "--dry-run leaves conf unmodified" "[ '$MTIME_BEFORE' = '$MTIME_AFTER' ]"

  # Test 2: normal run writes the [program:bus-catalog-guard] block.
  SUPERVISORD_USER_CONF="$FAKE_CONF" ZO_REPO="$TD" ZO_CATALOG_LOG="$TD/refresh.log" \
    SKIP_SUPERVISORCTL=1 bash "$0" >/dev/null 2>&1
  check "conf contains [program:bus-catalog-guard]" \
    "grep -q '\[program:bus-catalog-guard\]' '$FAKE_CONF'"
  check "conf contains marker" \
    "grep -q '$MARKER' '$FAKE_CONF'"

  # Test 3: idempotent -- second run does not duplicate the block.
  SUPERVISORD_USER_CONF="$FAKE_CONF" ZO_REPO="$TD" ZO_CATALOG_LOG="$TD/refresh.log" \
    SKIP_SUPERVISORCTL=1 bash "$0" >/dev/null 2>&1
  COUNT=$(grep -c '\[program:bus-catalog-guard\]' "$FAKE_CONF" || true)
  check "block appears exactly once after two runs" "[ '$COUNT' = '1' ]"

  # Report
  echo ""
  echo "self-test: ${PASS} passed, ${FAIL} failed"
  [ "$FAIL" = "0" ] && exit 0 || exit 1
fi

# ---------------------------------------------------------------------------
# Guard: refuse to install if the guard script is not on origin/main.
# The program block fetches it from there; a guard that only exists locally
# installs a program that can never do useful work.
# ---------------------------------------------------------------------------
if ! git -C "$REPO" fetch -q origin main 2>/dev/null; then
  warn "could not fetch origin/main; validating against the last known ref"
fi
if ! git -C "$REPO" show origin/main:tools/bus_catalog_guard.sh > /tmp/_guard_check_spv.sh 2>/dev/null; then
  bad "tools/bus_catalog_guard.sh is not on origin/main -- refusing to install"
  bad "  merge it first; the supervisord program fetches it from there"
  rm -f /tmp/_guard_check_spv.sh
  exit 2
fi
if ! bash -n /tmp/_guard_check_spv.sh 2>/dev/null; then
  bad "the guard on origin/main has shell syntax errors -- refusing to install"
  rm -f /tmp/_guard_check_spv.sh
  exit 2
fi
rm -f /tmp/_guard_check_spv.sh
ok "guard validated on origin/main"

# ---------------------------------------------------------------------------
# Build the [program:bus-catalog-guard] block.
# Pattern: temporal-context-writer's while/sleep loop, adapted for the guard.
# The guard is fetched from origin/main on each wake so the running copy always
# matches the reviewed state (audit finding B2: build workspace runs behind main).
# ---------------------------------------------------------------------------
FETCH_CMD="git -C $REPO fetch -q origin main && git -C $REPO show origin/main:tools/bus_catalog_guard.sh > /tmp/bus_catalog_guard.sh"
LOOP_CMD="while true; do $FETCH_CMD && bash /tmp/bus_catalog_guard.sh >> $LOG 2>&1; sleep 3600; done"

BLOCK="
[program:bus-catalog-guard]  $MARKER
command=bash -c '$LOOP_CMD'
directory=/home/workspace
environment=ZO_REPO=\"$REPO\"
autostart=true
autorestart=true
stopsignal=TERM
stopasgroup=true
startretries=5
startsecs=3
stdout_logfile=/dev/shm/bus-catalog-guard.log
stderr_logfile=/dev/shm/bus-catalog-guard_err.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=3
killasgroup=true
stopwaitsecs=4
stderr_logfile_maxbytes=10MB
stderr_logfile_backups=3"

if [ "$DRY" = "1" ]; then
  warn "DRY RUN -- would append to $CONF_FILE:"
  printf '%s\n' "$BLOCK"
  warn "Then run:"
  warn "  $SUPERVISOR_CTL -c $SUPERVISOR_CONF reread"
  warn "  $SUPERVISOR_CTL -c $SUPERVISOR_CONF update"
  warn "  $SUPERVISOR_CTL -c $SUPERVISOR_CONF status bus-catalog-guard"
  exit 0
fi

# ---------------------------------------------------------------------------
# Idempotent write: remove any previous managed block, then append the new one.
# The marker is on the [program:...] line itself so grepping for it is unique.
# ---------------------------------------------------------------------------
if [ ! -f "$CONF_FILE" ]; then
  bad "conf file not found: $CONF_FILE"
  exit 1
fi

# Remove the existing managed block (from [program:bus-catalog-guard] up to the
# next blank line that precedes a new [section], or end-of-file).
TMPFILE="$(mktemp)"
python3 - "$CONF_FILE" "$MARKER" "$TMPFILE" <<'PY'
import sys, re

path, marker, out_path = sys.argv[1:4]
text = open(path).read()

# Strip the previous managed block: from the line containing the marker
# to the first blank line that starts a new [section] header, or EOF.
# We match the full block generously so re-runs are always idempotent.
cleaned = re.sub(
    r'\n\[program:bus-catalog-guard\][^\n]*' + re.escape(marker) + r'.*?(?=\n\[|\Z)',
    '',
    text,
    flags=re.DOTALL,
)
open(out_path, 'w').write(cleaned)
PY

cp "$TMPFILE" "$CONF_FILE"
rm -f "$TMPFILE"

# Append the new block.
printf '%s\n' "$BLOCK" >> "$CONF_FILE"
ok "wrote [program:bus-catalog-guard] block to $CONF_FILE"

# ---------------------------------------------------------------------------
# Activate -- unless the caller opted out (used by --self-test).
# ---------------------------------------------------------------------------
SKIP="${SKIP_SUPERVISORCTL:-0}"
if [ "$SKIP" = "1" ]; then
  warn "SKIP_SUPERVISORCTL=1 -- skipping reread/update (self-test mode)"
  exit 0
fi

ok "running: $SUPERVISOR_CTL -c $SUPERVISOR_CONF reread"
"$SUPERVISOR_CTL" -c "$SUPERVISOR_CONF" reread

ok "running: $SUPERVISOR_CTL -c $SUPERVISOR_CONF update"
"$SUPERVISOR_CTL" -c "$SUPERVISOR_CONF" update

# Verify the program is now known.
STATUS=$("$SUPERVISOR_CTL" -c "$SUPERVISOR_CONF" status bus-catalog-guard 2>&1 || true)
if echo "$STATUS" | grep -qiE 'RUNNING|STARTING'; then
  ok "bus-catalog-guard is up: $STATUS"
  ok "heartbeat will appear in: /home/workspace/logs/bus_catalog_heartbeat.json"
  ok "log: /dev/shm/bus-catalog-guard.log"
  exit 0
elif echo "$STATUS" | grep -qi 'no such process'; then
  bad "bus-catalog-guard not recognised by supervisord -- reread may have failed"
  bad "  run manually: $SUPERVISOR_CTL -c $SUPERVISOR_CONF reread && $SUPERVISOR_CTL -c $SUPERVISOR_CONF update"
  exit 1
else
  warn "bus-catalog-guard status: $STATUS"
  warn "If the status is not RUNNING within ~10s, check /dev/shm/bus-catalog-guard_err.log"
  exit 0
fi
