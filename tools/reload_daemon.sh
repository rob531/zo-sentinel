#!/usr/bin/env bash
# reload_daemon.sh <name> -- reload a daemon_wrapper-managed daemon onto the
# CURRENT on-disk code, without the broad-pkill-kills-the-wrapper trap.
#
# THE TRAP: `pkill -f goose_runner.py` matches BOTH the python child AND its
# wrapper line (`bash daemon_wrapper.sh goose_runner .../goose_runner.py`), so
# the wrapper dies too and nothing respawns -> you must hand-relaunch every time.
#
# THIS: if the wrapper is alive, kill ONLY the python child (pattern excludes the
# bash wrapper); the wrapper respawns it from disk = the new code. If the wrapper
# is dead (cold start / a prior broad pkill), relaunch under a fresh wrapper.
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
# the child; \.py anchors so e.g. sentinel_directive_generator does not match
# sentinel_directive_generator_goose. The wrapper line has "<name> " before the
# script path, so the trailing space disambiguates the same way.
CHILD_PAT="python.*${NAME}\.py"
WRAP_PAT="daemon_wrapper.sh ${NAME} "

WRAPPER=$(pgrep -f "$WRAP_PAT" 2>/dev/null | head -1)

if [ -n "$WRAPPER" ]; then
  echo "wrapper alive (pid $WRAPPER) -- killing only the python child; it respawns on the new code"
  pkill -f "$CHILD_PAT" 2>/dev/null || echo "  (no running child to kill)"
else
  echo "no wrapper for $NAME -- relaunching under a fresh daemon_wrapper"
  pkill -f "$CHILD_PAT" 2>/dev/null || true
  sleep 1
  nohup bash "$MESH/daemon_wrapper.sh" "$NAME" "$SCRIPT" >> "$LOGS/$NAME.log" 2>&1 &
fi

# Confirm it came back (wrappers may back off a few seconds).
for _ in 1 2 3 4 5; do
  sleep 1
  PID=$(pgrep -f "$CHILD_PAT" 2>/dev/null | head -1)
  [ -n "${PID:-}" ] && break
done
if [ -n "${PID:-}" ]; then
  echo "OK: $NAME running (pid $PID) on $SCRIPT"
else
  echo "WARN: $NAME not up yet -- check $LOGS/$NAME.log (wrapper may still be backing off)"
fi
