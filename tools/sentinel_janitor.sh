#!/usr/bin/env bash
# sentinel_janitor.sh -- periodic, idempotent maintenance for the build->publish
# pipeline. Invoked each tick by watchdog.sh (v3.8+), NOT by go.sh.
#
# WHY HERE AND NOT go.sh: go.sh's bootstrap is already overloaded and fragile --
# every recurring-maintenance addition to it has historically made it hang and
# destabilise the container on the next boot (see tools/harden_go_sh.py,
# patch_go_sh*.py). Recurring maintenance belongs behind the existing watchdog
# scheduler (watchdog_daemon.py runs watchdog.sh every ~6-9 min), which is
# decoupled from boot. This script is the "scheduled task" the container lacks a
# cron for.
#
# Fixes TWO independent failure modes, both of which silently halt PR output
# after a couple of days of running:
#
#   1. GHOST .done GRAVEYARD (build side). goose_runner skips any directive whose
#      <id>.done.json sentinel exists ("non-eligible"). Ghost sentinels -- goose
#      process exited 0 but produced no file (the pre-#23 502 era + the
#      goose-success-without-file bug) -- permanently mark real directives as
#      "already built", so after enough accumulate EVERY queued directive is
#      skipped and the build loop starves (0 new build_artifacts -> nothing to
#      publish). We run tools/sweep_ghost_done.py --apply, which conservatively
#      deletes ONLY sentinels whose declared output is genuinely absent on disk
#      (a real build is never un-marked). build_completion.py already PREVENTS new
#      ghosts; this REMEDIATES any that still slip through, on a schedule.
#
#   2. MODULE-SHADOW CRASH-LOOP (publish side). The publisher / ingestor /
#      governor run as `python3 -m zo_sentinel.<mod>`. /home/workspace/zo_sentinel
#      is ITSELF an importable `zo_sentinel` package (it has __init__.py), and the
#      real submodules live one level down in zo_sentinel/zo_sentinel/. Under
#      `python -m`, cwd is searched on sys.path BEFORE PYTHONPATH, so when a loop
#      is launched WITHOUT cwd=$SENTINEL (e.g. a `zm go` that SOURCED go.sh from
#      /home/workspace) `import zo_sentinel` resolves to the outer shadow package,
#      which lacks publisher/ingestor -> dies every cycle with
#      `No module named zo_sentinel.<mod>`. We detect that exact signature in each
#      loop's log and relaunch it with the cwd fix (cd $SENTINEL && ...), the same
#      idiom watchdog.sh already uses for proposed_to_pending_promoter.
#
# Idempotent + non-thrashing: a loop is touched ONLY when its process is absent
# OR its log shows the import error. A healthy loop is left strictly alone, so
# in-flight builds/publishes are never interrupted. Safe to run every tick.
set -u

SENTINEL=/home/workspace/zo_sentinel
LOGS=/home/workspace/logs
TS=$(date '+%Y-%m-%d %H:%M:%S')
log() { echo "[$TS] sentinel_janitor: $1"; }

cd "$SENTINEL" 2>/dev/null || { log "FATAL: $SENTINEL not found"; exit 1; }

# --- 1. ghost .done sweep (throttled to ~hourly) --------------------------
# Cheap + idempotent, but throttled so the janitor log isn't spammed every tick.
STAMP="$LOGS/.sentinel_janitor_sweep_stamp"
now=$(date +%s)
last=0; [ -f "$STAMP" ] && last=$(stat -c %Y "$STAMP" 2>/dev/null || echo 0)
if [ $(( now - last )) -ge 3600 ]; then
    log "running ghost .done sweep (sweep_ghost_done.py --apply)"
    PYTHONPATH="$SENTINEL" python3 tools/sweep_ghost_done.py --apply 2>&1 | sed 's/^/  sweep: /'
    : > "$STAMP"
fi

# --- 2. heal the python -m poll-loops (import crash-loop / absent) ---------
# heal <pgrep-guard-substring> <logfile> <relaunch-command>
# guard substrings appear in the persistent process argv (the bash -c while-loop
# for publisher/governor, or the exec'd python for the ingestor --interval
# poller), so pgrep finds the loop even during its sleep window.
heal() {
    local guard="$1" logfile="$2" relaunch="$3"
    local broken=0 reason=""
    if ! pgrep -f "$guard" >/dev/null 2>&1; then
        broken=1; reason="no process"
    elif tail -n 8 "$LOGS/$logfile" 2>/dev/null | grep -q 'No module named zo_sentinel'; then
        broken=1; reason="import crash-loop (No module named)"
    fi
    if [ "$broken" -eq 1 ]; then
        log "[$guard] BROKEN ($reason) -- relaunching with cwd fix"
        pkill -f "$guard" 2>/dev/null || true
        sleep 1
        eval "$relaunch"
    fi
}

# Publisher: delegate to the blessed relauncher (handles clone dir + dormancy +
# the cd fix). It pkills + backgrounds its own loop and returns in ~4s.
heal 'zo_sentinel.publisher run-once' pr_publisher.log \
    "bash $SENTINEL/tools/run_publisher_daemon.sh"

# Ingestor: persistent --interval poller; exec so the running cmdline is plain
# python3 -m ... (pgrep guard 'zo_sentinel.ingestor run' matches it).
heal 'zo_sentinel.ingestor run' artifact_ingestor.log \
    "nohup env PYTHONPATH=$SENTINEL bash -c 'cd $SENTINEL && exec python3 -m zo_sentinel.ingestor run --interval 300' >> $LOGS/artifact_ingestor.log 2>&1 &"

# Governor: one-shot 'govern' wrapped in an external sleep loop (the bash -c argv
# carries 'zo_sentinel.ingestor govern', so pgrep matches across the sleep).
heal 'zo_sentinel.ingestor govern' activation_governor.log \
    "nohup env PYTHONPATH=$SENTINEL bash -c 'cd $SENTINEL && while true; do python3 -m zo_sentinel.ingestor govern; sleep 600; done' >> $LOGS/activation_governor.log 2>&1 &"

log "tick complete"
