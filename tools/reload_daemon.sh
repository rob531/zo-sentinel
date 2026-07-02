#!/usr/bin/env bash
# reload_daemon.sh <name> -- reload a daemon_wrapper-managed daemon onto the
# CURRENT on-disk code, DETERMINISTICALLY: one invocation either verifies the
# daemon is running on fresh code (exit 0) or tells you it is down (exit 1).
#
# THE TWO TRAPS THIS VERSION CLOSES (both bit us live on 2026-07-02):
#   1. THE BROAD-PKILL TRAP (original): `pkill -f goose_runner.py` matches the
#      wrapper's own cmdline too, killing the supervisor. Still avoided via
#      child-only patterns ("python.*<name>.py" never matches the bash wrapper).
#   2. THE GRACEFUL-EXIT-0 TRAP (root-caused today): daemons with SIGTERM
#      handlers (goose_runner, sentinel_directive_generator_goose) exit rc=0
#      on the reload kill, and daemon_wrapper's clean-exit contract reads rc=0
#      as "stop" -- so killing the child stopped the WRAPPER as well, silently
#      orphaning the daemon (twice today, ~4min outages). Fix: the RELOAD
#      MARKER protocol -- touch /home/workspace/zo_mesh/.reload_<name> BEFORE
#      the kill; the (patched) wrapper consumes it and respawns regardless of
#      rc. A post-verify + cold-relaunch fallback makes the script converge
#      even under an UNPATCHED wrapper (defense in depth), and it exits
#      NONZERO if the daemon is still down -- no more trusting "OK" lines.
#
# Usage:
#   bash tools/reload_daemon.sh goose_runner
#   bash tools/reload_daemon.sh sentinel_directive_generator_goose
set -uo pipefail

NAME="${1:?usage: reload_daemon.sh <daemon-basename, e.g. goose_runner>}"
NAME="${NAME%.py}"
MESH="/home/workspace/zo_mesh"
SENTINEL="/home/workspace/zo_sentinel"
LOGS="/home/workspace/logs"

# Resolve the script path (sentinel first, then mesh).
if   [ -f "$SENTINEL/$NAME.py" ]; then SCRIPT="$SENTINEL/$NAME.py"
elif [ -f "$MESH/$NAME.py" ];     then SCRIPT="$MESH/$NAME.py"
else echo "ERROR: $NAME.py not found in $SENTINEL or $MESH"; exit 2; fi

# Patterns: the bash wrapper has no "python", so "python.*<name>.py" hits ONLY
# the child; the wrapper line has "<name> " (trailing space) after
# daemon_wrapper.sh, which disambiguates sibling names the same way.
CHILD_PAT="python.*${NAME}\.py"
WRAP_PAT="daemon_wrapper.sh ${NAME} "
MARKER="$MESH/.reload_${NAME}"

OLD_CHILD=$(pgrep -f "$CHILD_PAT" 2>/dev/null | head -1 || true)
WRAPPER=$(pgrep -f "$WRAP_PAT" 2>/dev/null | head -1 || true)

if [ -n "$WRAPPER" ]; then
  touch "$MARKER"
  echo "wrapper alive (pid $WRAPPER) -- reload marker set; killing python child (old pid ${OLD_CHILD:-none})"
  pkill -f "$CHILD_PAT" 2>/dev/null || echo "  (no running child to kill)"
else
  echo "no wrapper for $NAME -- going straight to cold relaunch"
fi

# Post-verify: a NEW child pid (different from the one we killed) within 25s.
NEW=""
for _ in $(seq 1 25); do
  sleep 1
  P=$(pgrep -f "$CHILD_PAT" 2>/dev/null | head -1 || true)
  if [ -n "$P" ] && [ "$P" != "${OLD_CHILD:-__none__}" ]; then NEW="$P"; break; fi
done

# Fallback: cold relaunch under a FRESH wrapper (covers wrapper-dead,
# unpatched-wrapper, and back-off-forever cases).
if [ -z "$NEW" ]; then
  echo "child did not respawn -- cold-relaunching a fresh wrapper"
  rm -f "$MARKER"
  pkill -f "$WRAP_PAT" 2>/dev/null || true
  pkill -f "$CHILD_PAT" 2>/dev/null || true
  sleep 1
  nohup bash "$MESH/daemon_wrapper.sh" "$NAME" "$SCRIPT" >> "$LOGS/$NAME.log" 2>&1 &
  for _ in $(seq 1 20); do
    sleep 1
    NEW=$(pgrep -f "$CHILD_PAT" 2>/dev/null | head -1 || true)
    [ -n "$NEW" ] && break
  done
fi

# Tidy: if the wrapper respawned without passing its exit branch, the marker
# may linger and would turn the NEXT genuine stop into one spurious respawn.
rm -f "$MARKER"

if [ -n "$NEW" ]; then
  echo "OK: $NAME running (pid $NEW) on $SCRIPT"
  exit 0
fi
echo "FAIL: $NAME is NOT running -- check $LOGS/$NAME.log and $LOGS/wrapper_${NAME}.log"
exit 1
