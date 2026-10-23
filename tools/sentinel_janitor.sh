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
# Fixes THREE independent failure modes that silently halt PR output:
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
#   3. KEYLESS LADDER SHIM (build side, post-reboot). go.sh launches ladder_shim
#      BARE on boot; its key_hydrator self-hydrate times out, so RcGeminiAPIKey is
#      unresolved AND the rung-0 MiniMax key the low/med BUILDERS use is absent ->
#      every goose build GHOSTS (model unreachable) -> directives get .failed ->
#      the build pipeline silently dies. relaunch_ladder_keyed.sh rewires the shim
#      onto /root/.zo_secrets + secretless-ai but is NOT durable across reboot, so
#      we detect the keyless signature in ladder_shim.log and re-key it here.
#
# Idempotent + non-thrashing: a loop is touched ONLY when its process is absent
# OR its log shows the import error; the rekey fires ONLY on the keyless signature
# and is throttled. Healthy state is left strictly alone, so in-flight
# builds/publishes are never interrupted. Safe to run every tick.
set -u

SENTINEL=/home/workspace/zo_sentinel
LOGS=/home/workspace/logs
TS=$(date '+%Y-%m-%d %H:%M:%S')
log() { echo "[$TS] sentinel_janitor: $1"; }

cd "$SENTINEL" 2>/dev/null || { log "FATAL: $SENTINEL not found"; exit 1; }
now=$(date +%s)

# --- 1. ghost .done sweep (throttled to ~hourly) --------------------------
# Cheap + idempotent, but throttled so the janitor log isn't spammed every tick.
STAMP="$LOGS/.sentinel_janitor_sweep_stamp"
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

# --- 2b. one-shot promoter restart (deploy merged code onto the running loop) --
# proposed_to_pending_promoter is a healthy long-running SINGLETON; nothing in the
# recovery allowlist restarts a HEALTHY promoter (reload_daemon excludes it;
# watchdog.sh only acts on pgrep count==0 or >1). So a merged code change to the
# promoter (e.g. #187's terminal-collision archival, which stops stuck ghosts from
# starving the per-cycle cap) cannot reach the already-running process. Drop a
# one-shot sentinel (touch /home/workspace/logs/promoter_RESTART) and this cycles
# the promoter onto the current on-disk code, then deletes the sentinel so it
# fires exactly once. Same pgrep + relaunch idiom watchdog.sh uses for this daemon
# (the watchdog's own _daemon check ran earlier this tick, so no double-launch).
if [ -f "$LOGS/promoter_RESTART" ]; then
    log "promoter_RESTART sentinel present -- cycling proposed_to_pending_promoter onto current code"
    rm -f "$LOGS/promoter_RESTART"
    pkill -f 'python.*proposed_to_pending_promoter' 2>/dev/null || true
    sleep 2
    nohup bash -c "cd $SENTINEL && exec python3 -m zo_sentinel.promoters.proposed_to_pending_promoter" \
        >> "$LOGS/proposed_to_pending_promoter.log" 2>&1 &
    log "promoter relaunched (pid $!)"
fi

# --- 3. ensure the ladder_shim is KEYED (re-key if it booted bare) ---------
# After a reboot go.sh starts ladder_shim BARE -> key_hydrator times out ->
# RcGeminiAPIKey unresolved AND the rung-0 MiniMax builder key is absent -> every
# goose build ghosts -> .failed graveyard. relaunch_ladder_keyed.sh fixes it but
# isn't durable, so re-key here when the shim log shows the keyless signature.
# Throttled (>=600s) so a genuinely secret-less box can't thrash the shim (the
# rekey script itself also pre-verifies and falls back to bare on its own).
REKEY="$SENTINEL/relaunch_ladder_keyed.sh"
RK_STAMP="$LOGS/.sentinel_janitor_rekey_stamp"
if [ -f "$REKEY" ]; then
    shim_tail=$(tail -n 30 "$LOGS/ladder_shim.log" 2>/dev/null)
    rk_last=0; [ -f "$RK_STAMP" ] && rk_last=$(stat -c %Y "$RK_STAMP" 2>/dev/null || echo 0)
    if echo "$shim_tail" | grep -q 'RcGeminiAPIKey.*unresolved' \
       && ! echo "$shim_tail" | grep -q 'keys hydrated' \
       && [ $(( now - rk_last )) -ge 600 ]; then
        log "ladder_shim KEYLESS (RcGeminiAPIKey unresolved) -- re-keying (relaunch_ladder_keyed.sh)"
        bash "$REKEY" 2>&1 | sed 's/^/  rekey: /'
        : > "$RK_STAMP"
    fi
fi

# 4. REPO-ROOT PACKAGE MARKER MUTATION (2026-08-22, daily-chairman-review).
#    zo_sentinel/__init__.py is a bare package marker by contract. Twice now
#    (GH #3415, 2026-08-13..16, a three-day total build outage; and again by
#    2026-08-22) it was overwritten with an "Auto-emitted service package" body
#    carrying ~20 `from .X import Y` lines naming modules that do not exist, so
#    `import zo_sentinel` raises ModuleNotFoundError and EVERY
#    `python3 -m zo_sentinel.*` entrypoint dies at package init. The running
#    promoter survives because it holds the pre-mutation module in memory --
#    which is exactly why nothing alarms until a restart, and then a restart
#    loop that never yields a surviving process looks, from outside, identical
#    to a healthy one.
#    The WRITER is not yet identified, so this guard tests the OUTCOME (does the
#    package import?) rather than policing a writer, and repairs from HEAD.
#    Idempotent; exits 0 on a clean tree; snapshots the mutation before repair.
PMG="$SENTINEL/ops/host/package_marker_guard.py"
if [ -f "$PMG" ]; then
    python3 "$PMG" 2>&1 | sed 's/^/  marker_guard: /' || log "package_marker_guard NONZERO -- markers still broken after repair"
fi

# 5. PIPELINE LIVENESS ALARM (GH #3415 prevention 3, FU-349). Outcome-based:
#    live pending work + no <id>.done.json stamped at the directives ROOT
#    within 2h -> alarm loudly and latch $LOGS/PIPELINE_STALLED (JSON basis
#    inside) for other lanes to read; auto-cleared when completions flow
#    again. Read-only and report-loud: it gates and restarts NOTHING --
#    healing is sections 1-4's job, making a stall impossible to miss is
#    this one's. Would have caught the 08-13..16 outage the first morning,
#    whatever the mechanism.
PLG="$SENTINEL/ops/host/pipeline_liveness_guard.py"
if [ -f "$PLG" ]; then
    python3 "$PLG" 2>&1 | sed 's/^/  liveness_guard: /'
    plg_rc=${PIPESTATUS[0]:-0}
    [ "$plg_rc" -eq 1 ] && log "PIPELINE LIVENESS ALARM -- live pending work and no completion in 2h (basis: $LOGS/PIPELINE_STALLED)"
    [ "$plg_rc" -eq 2 ] && log "pipeline_liveness_guard CANNOT-EVALUATE (rc=2) -- unknown is not healthy"
fi

log "tick complete"
