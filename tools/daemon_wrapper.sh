#!/bin/bash
[ -f /home/workspace/zo_mesh/.zo_env ] && source /home/workspace/zo_mesh/.zo_env
# daemon_wrapper.sh -- respawn wrapper for any ZOMesh python daemon.
#
# LIVE PATH: /home/workspace/zo_mesh/daemon_wrapper.sh
# CANONICAL SOURCE: tools/daemon_wrapper.sh in the zo-sentinel repo (tracked
# since 2026-07-02). Keep the two in sync: deploy = copy this file over the
# live path (backup first).
#
# Usage:
#   exec bash daemon_wrapper.sh <name> <python_script_abspath>
#   exec bash daemon_wrapper.sh <name> -m <dotted.module.path>
#
# Behavior:
#   - Runs the target script in a restart loop
#   - Honors a max-restart-rate ceiling: if the daemon crashes >5 times
#     within 60 seconds, the wrapper sleeps 5 minutes to avoid tight spin
#   - Logs every restart + exit code to /home/workspace/logs/wrapper_<name>.log
#   - RELOAD PROTOCOL (2026-07-02): if /home/workspace/zo_mesh/.reload_<name>
#     exists when the child exits, this is a code RELOAD, not a stop --
#     consume the marker and respawn REGARDLESS of exit code. This closes the
#     orphaning root cause: graceful daemons (goose_runner, the generator)
#     trap SIGTERM and exit rc=0, which the clean-exit contract below would
#     otherwise read as "stop", so tools/reload_daemon.sh's kill-the-child
#     left the daemon down twice on 2026-07-02.
#   - Preserves exit code 0 (clean shutdown, no marker) as "do not respawn"
#   - Any other exit code = respawn
#
# Expected launch via go.sh or manually:
#   nohup bash /home/workspace/zo_mesh/daemon_wrapper.sh \
#     zo_sentinel_builder \
#     /home/workspace/zo_sentinel/zo_sentinel_builder.py \
#     >> /home/workspace/logs/zo_sentinel_builder.log 2>&1 &

set -u  # undefined var = fatal (but NOT -e; we want to handle exit codes)

NAME="${1:-}"
SCRIPT="${2:-}"
shift 2 || true
EXTRA_ARGS=("$@")

if [[ -z "$NAME" || -z "$SCRIPT" ]]; then
  echo "usage: daemon_wrapper.sh <name> <python_script_abspath> [args...]" >&2
  exit 2
fi

# MODULE MODE (FU-121, 2026-07-27). The SOA re-organisation is moving daemons
# into packages -- `python3 -m zo_sentinel.promoters.proposed_to_pending_promoter`
# is already live -- and this wrapper could only ever run a top-level FILE, so
# those daemons run with NO supervision and no reload protocol at all. Accept a
# module spec as the second argument, exactly as python does:
#     bash daemon_wrapper.sh <name> -m zo_sentinel.promoters.<mod> [cwd]
# Purely additive: a path argument behaves byte-identically to before.
RUN_MODE="file"
if [[ "$SCRIPT" == "-m" ]]; then
  RUN_MODE="module"
  MODULE="${EXTRA_ARGS[0]:-}"
  EXTRA_ARGS=("${EXTRA_ARGS[@]:1}")
  if [[ -z "$MODULE" ]]; then
    echo "[$NAME wrapper] ERROR: -m given with no module name" >&2
    exit 2
  fi
elif [[ ! -f "$SCRIPT" ]]; then
  echo "[$NAME wrapper] ERROR: script not found: $SCRIPT" >&2
  exit 2
fi

WRAPPER_LOG="/home/workspace/logs/wrapper_${NAME}.log"
mkdir -p "$(dirname "$WRAPPER_LOG")"

RELOAD_MARKER="/home/workspace/zo_mesh/.reload_${NAME}"

# Rolling window of crash timestamps (seconds since epoch)
CRASH_TIMES=()
MAX_CRASHES_PER_WINDOW=5
WINDOW_SEC=60
BACKOFF_SEC=300  # 5 minute cool-down if threshold exceeded

log() {
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] [${NAME} wrapper] $*" >> "$WRAPPER_LOG"
}

if [[ "$RUN_MODE" == "module" ]]; then
  log "wrapper starting for module $MODULE (args: ${EXTRA_ARGS[*]:-none})"
else
  log "wrapper starting for $SCRIPT (args: ${EXTRA_ARGS[*]:-none})"
fi

ATTEMPT=0
while true; do
  ATTEMPT=$((ATTEMPT + 1))
  log "starting attempt #${ATTEMPT}"
  START_TS=$(date +%s)

  # Run the daemon; let its own stdout/stderr go to wherever the parent
  # (nohup) is redirecting, NOT to the wrapper log.
  if [[ "$RUN_MODE" == "module" ]]; then
    python3 -m "$MODULE" "${EXTRA_ARGS[@]}"
  else
    python3 "$SCRIPT" "${EXTRA_ARGS[@]}"
  fi
  RC=$?
  END_TS=$(date +%s)
  RAN_FOR=$((END_TS - START_TS))

  log "attempt #${ATTEMPT} exited rc=${RC} after ${RAN_FOR}s"

  # Reload protocol: marker present = this exit is a RELOAD (reload_daemon.sh
  # set it before killing the child). Consume it and respawn on the current
  # on-disk code regardless of rc -- a reload is not a crash and not a stop.
  if [[ -f "$RELOAD_MARKER" ]]; then
    rm -f "$RELOAD_MARKER"
    log "reload marker honored (rc=${RC}); respawning on current code"
    sleep 1
    continue
  fi

  # Clean shutdown: do not respawn
  if [[ $RC -eq 0 ]]; then
    log "clean exit (rc=0); wrapper stopping"
    exit 0
  fi

  # Track crash time for rate limiting
  NOW=$(date +%s)
  CRASH_TIMES+=($NOW)

  # Prune crashes older than window
  PRUNED=()
  for t in "${CRASH_TIMES[@]}"; do
    if [[ $((NOW - t)) -lt $WINDOW_SEC ]]; then
      PRUNED+=($t)
    fi
  done
  CRASH_TIMES=("${PRUNED[@]}")

  if [[ ${#CRASH_TIMES[@]} -ge $MAX_CRASHES_PER_WINDOW ]]; then
    log "${#CRASH_TIMES[@]} crashes in ${WINDOW_SEC}s; backing off ${BACKOFF_SEC}s"
    sleep $BACKOFF_SEC
    CRASH_TIMES=()  # reset; we've paid our penalty
  else
    # Graduated backoff: 2s, 4s, 8s... capped at 30s
    BACKOFF=$((2 ** (${#CRASH_TIMES[@]} - 1)))
    [[ $BACKOFF -gt 30 ]] && BACKOFF=30
    log "respawning in ${BACKOFF}s (crash ${#CRASH_TIMES[@]} of ${MAX_CRASHES_PER_WINDOW} in window)"
    sleep $BACKOFF
  fi
done
