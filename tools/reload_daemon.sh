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

# --- target resolution ------------------------------------------------------
# FU-121 (2026-07-27): this resolver looked ONLY for a top-level <name>.py in
# two flat directories, so `reload_daemon.sh proposed_to_pending_promoter`
# died with "not found" on a daemon that was demonstrably alive (pid 16923,
# `python3 -m zo_sentinel.promoters.proposed_to_pending_promoter`). It simply
# runs as a MODULE. That made the entire zo_sentinel.promoters.* namespace --
# and every future package-structured daemon, which is the direction SOA is
# moving everything -- unreloadable by the mesh's own reload protocol (#1156),
# and left the standing act-authority "reload an idle daemon" unexecutable for
# exactly the class of daemon we are building more of.
#
# Resolution order. The flat-FILE cases come first and are byte-identical to
# before, so no existing call changes behaviour:
#   1. $SENTINEL/<name>.py           (file mode -- unchanged)
#   2. $MESH/<name>.py               (file mode -- unchanged)
#   3. <name>.py nested in a package under either root -> module mode, with
#      the dotted path derived by walking up while __init__.py exists
#   4. no file anywhere, but a LIVE process running `-m <...>.<name>` -> take
#      the module path verbatim from the running cmdline. The running process
#      is the most reliable source of truth we have about how to restart it.
MODE="file"; SCRIPT=""; MODULE=""; RUN_CWD=""

# Derive `pkg.sub.name` + the directory to run it from, by walking up while
# __init__.py is present -- the same rule python itself uses.
_derive_module() {
  local f="$1" d mod base
  d="$(cd "$(dirname "$f")" && pwd)"
  mod="$(basename "$f" .py)"
  while [ -f "$d/__init__.py" ]; do
    base="$(basename "$d")"
    mod="$base.$mod"
    d="$(dirname "$d")"
  done
  MODULE="$mod"; RUN_CWD="$d"
}

if   [ -f "$SENTINEL/$NAME.py" ]; then SCRIPT="$SENTINEL/$NAME.py"
elif [ -f "$MESH/$NAME.py" ];     then SCRIPT="$MESH/$NAME.py"
else
  # 3. nested module file
  NESTED="$(find "$SENTINEL" "$MESH" -maxdepth 4 -name "$NAME.py" -type f \
             -not -path "*/__pycache__/*" -not -path "*/site-packages/*" \
             -not -path "*/node_modules/*" -not -path "*/.git/*" \
             2>/dev/null | head -1 || true)"
  if [ -n "$NESTED" ]; then
    MODE="module"; SCRIPT="$NESTED"; _derive_module "$NESTED"
  else
    # 4. ask the running process how it was started
    LIVE="$(pgrep -af "python.* -m ([A-Za-z0-9_]+\.)*${NAME}( |$)" 2>/dev/null \
            | head -1 || true)"
    if [ -n "$LIVE" ]; then
      MODE="module"
      MODULE="$(echo "$LIVE" | sed -nE 's/.* -m ([A-Za-z0-9_.]+).*/\1/p')"
      RUN_CWD="$SENTINEL"
    fi
  fi
fi

if [ "$MODE" = "file" ] && [ -z "$SCRIPT" ]; then
  echo "ERROR: $NAME not found as $SENTINEL/$NAME.py, $MESH/$NAME.py, a nested"
  echo "       module under either root, or a running 'python -m ...$NAME'"
  exit 2
fi
if [ "$MODE" = "module" ] && [ -z "$MODULE" ]; then
  echo "ERROR: $NAME looked like a module daemon but no module path resolved"
  exit 2
fi

# Patterns: the bash wrapper has no "python", so "python.*<name>.py" hits ONLY
# the child; the wrapper line has "<name> " (trailing space) after
# daemon_wrapper.sh, which disambiguates sibling names the same way.
# In module mode the child is matched on its `-m <dotted.path>` form instead --
# a module daemon has no "<name>.py" anywhere in its cmdline, which is the
# whole reason the old pattern could never see it.
if [ "$MODE" = "module" ]; then
  CHILD_PAT="python.* -m ([A-Za-z0-9_]+\.)*${NAME}( |$)"
  TARGET_DESC="module $MODULE (cwd $RUN_CWD)"
else
  CHILD_PAT="python.*${NAME}\.py"
  TARGET_DESC="$SCRIPT"
fi
WRAP_PAT="daemon_wrapper.sh ${NAME} "
MARKER="$MESH/.reload_${NAME}"

# Resolution is the half that was broken, so make it independently checkable
# without killing anything: RELOAD_DAEMON_RESOLVE_ONLY=1 prints what it found
# and exits. This is what the test suite drives.
if [ "${RELOAD_DAEMON_RESOLVE_ONLY:-0}" = "1" ]; then
  echo "mode=$MODE"
  echo "module=$MODULE"
  echo "script=$SCRIPT"
  echo "run_cwd=$RUN_CWD"
  echo "child_pat=$CHILD_PAT"
  exit 0
fi

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
  if [ "$MODE" = "module" ]; then
    # Relaunched UNDER the wrapper (module support added in the same change),
    # so a module daemon gains the respawn + reload-marker protocol every
    # file daemon already has instead of running orphaned off PID 1.
    ( cd "$RUN_CWD" && nohup bash "$MESH/daemon_wrapper.sh" "$NAME" -m "$MODULE" \
        >> "$LOGS/$NAME.log" 2>&1 & )
  else
    nohup bash "$MESH/daemon_wrapper.sh" "$NAME" "$SCRIPT" >> "$LOGS/$NAME.log" 2>&1 &
  fi
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
  echo "OK: $NAME running (pid $NEW) on $TARGET_DESC"
  exit 0
fi
echo "FAIL: $NAME is NOT running -- check $LOGS/$NAME.log and $LOGS/wrapper_${NAME}.log"
exit 1
