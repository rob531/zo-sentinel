#!/bin/zsh
# watchdog.v3.10 - autonomous self-healer for ZOMesh
#
# CHANGELOG vs v3.9 (this change, 2026-08-22, GH #3415 prevention 1 / FU-349):
#   - FIX: a restart was recorded as a repair the moment the relaunch command
#     was issued. During the 2026-08-13..16 outage every restarted promoter
#     died at package init in <3s and the watchdog faithfully 'repaired' it
#     for three days -- from outside, a process that never starts looks
#     identical to one that is running. Every restart this tick is now
#     RE-OBSERVED ~10s later (_verify_restarts, one sleep for the whole
#     batch): a daemon with no surviving process, or a service whose /health
#     is still non-200, is logged as <name>_restart_FAILED and flips
#     HEALTHY=false. A restart that yields no surviving process is a
#     FAILURE, not a recovery. Census of same-shape sites in this commit:
#     _svc, _daemon, _daemon_tp, WorldAgent, IntentEngine here, plus
#     ops/host/restart_promoter.sh (which also gains --restart + outcome
#     verification).
#
# CHANGELOG vs v3.8 (this change) -- FORWARD-PORTED into the committed copy
# 2026-07-30; this fix has been LIVE on /home/workspace/zo_mesh/watchdog.sh
# since 2026-06-14 but was never in git: PR #147 (0c05bf30, 2026-07-02) added
# the janitor from a pre-v3.9 base and silently REGRESSED the committed copy
# back past it. The committed artifact was therefore a REGRESSION of the
# running artifact, and the only thing preventing the 2026-06-13/14 tick-freeze
# outage from returning was that this path has no deploy step. Restored so
# `diff ops/zo_mesh/watchdog.sh /home/workspace/zo_mesh/watchdog.sh` is empty.
#   - FIX: the two service health-check curls (_svc, _bw_check) had NO timeout,
#     so when WriteService :8772 /health wedged (its /query kept serving) the
#     un-timed curl blocked FOREVER and froze the ENTIRE tick -> the whole
#     watchdog hung, the promoter cohort it supervises died unrestarted, and the
#     build pipeline went idle (2026-06-13 18:41 and 2026-06-14 07:20). Added
#     `-m 5` (and `--connect-timeout 3`) so a wedged endpoint can never hang the
#     tick again; a non-200/timeout just trips the existing restart branch.
#
# CHANGELOG vs v3.7:
#   - ADD: build->publish pipeline janitor, invoked each tick (see the
#     `sentinel_janitor` hook near the end + tools/sentinel_janitor.sh). It (a)
#     sweeps the GHOST .done graveyard that makes goose_runner skip every
#     directive as "non-eligible" (build side goes idle after ~2-3 days), and (b)
#     heals the publisher/ingestor/governor `python3 -m zo_sentinel.<mod>` loops
#     when they crash-loop on `No module named zo_sentinel.<mod>` (the cwd/
#     module-shadow bug a sourced `zm go` reintroduces on every boot). Both used
#     to require a manual host relaunch each time; now they self-heal here.
#     DELIBERATELY placed here, NOT in go.sh: go.sh's bootstrap is overloaded and
#     hangs the container when extended (harden_go_sh.py / patch_go_sh*.py
#     graveyard). The watchdog tick is the container's standing "scheduled task".
#
# CHANGELOG vs v3.6 (shipped 2026-06-09 via tower MCP bridge):
#   - ADD: proposed_to_pending_promoter to _daemon coverage. It promotes
#     generated directives proposed/ -> pending/; goose_runner builds only what
#     lands in pending/. It is NOT watchdog-covered historically, so when it died
#     (~2026-06-09 02:59, silent, no traceback) it stayed dead, 20h of generated
#     directives piled up unpromoted, and the build pipeline went idle (0 builds).
#     Runs as a module (`python3 -m zo_sentinel.promoters.proposed_to_pending_promoter`)
#     so it needs cwd=$SENTINEL -> launched via `bash -c 'cd .. && exec python3 -m ..'`
#     (exec => the running cmdline is plain python3 -m ..., which the
#     'python.*<script>' pgrep guard matches; no lingering wrapper).
#
# CHANGELOG vs v3.5 (shipped 2026-06-09 via tower MCP bridge):
#   - CHANGE: GooseRunner now relaunches with `env ZO_ESCALATE=1` so the Phase-5
#     escalation edge SURVIVES a watchdog respawn. The watchdog does NOT source
#     $MESH/.zo_env (where go.sh keeps the flag on every `zm go`), so without
#     this a goose_runner crash would bring it back with escalation OFF until the
#     next full boot. The `env VAR=v` prefix is required because
#     `nohup ZO_ESCALATE=1 python3 ...` would try to exec the assignment itself;
#     `nohup env ZO_ESCALATE=1 python3 ...` is the correct form (env then execs
#     python3, so the running cmdline is still `python3 ...goose_runner.py` and
#     the pgrep guard still matches). To DISABLE escalation: revert this one line
#     to plain `python3 $SENTINEL/goose_runner.py` AND drop ZO_ESCALATE from
#     .zo_env.
#
# CHANGELOG vs v3.4 (shipped 2026-06-08 via tower MCP bridge):
#   - ADD: GooseRunner (zo_sentinel/goose_runner.py) to _daemon coverage.
#     It runs directly as `python3 goose_runner.py` (no args, while-True
#     loop, self-manages its OWN PID file; it is NOT wrapped by
#     daemon_wrapper and has no wrapper_goose_runner.log, so it is NOT a
#     TRUST_PIPELINE member). It was entirely unsupervised: after SIGTERM
#     on 2026-06-08 ~11:36Z it removed its PID file and never came back
#     (supervisord under /etc/zo does NOT manage mesh daemons -- this
#     watchdog does). pgrep guard "python.*goose_runner.py" now restarts
#     it each tick when absent. Uses _daemon (no uptime gate); a stale-PID
#     crash-loop would self-limit to at most one relaunch per 15min tick.
#
# CHANGELOG vs v3.3 (shipped 2026-04-29 ~09:30 UTC):
#   - FIX: pgrep -f patterns now anchored to "python.*<script>" so the
#     daemon_wrapper.sh process (which carries the script path as an arg)
#     no longer counts as a duplicate. v3.3 was mass-deduping all 10
#     trust pipeline daemons + WorldAgent every 15min for ~7+ hours.
#   - FIX: every "|| echo 0" replaced with "${var:-0}" pattern so a count
#     variable can never become a multi-line string. Was causing
#     "bad math expression: operator expected at `0'" rc=1 every tick,
#     which prevented _self_heartbeat and the "all healthy" log line
#     from ever firing.
#   - pkill scopes also anchored to "python.*<script>" so we don't kill
#     the daemon_wrapper supervisor when scrubbing duplicates.
#
# CHANGELOG vs v3.2:
#   - Added 10 trust pipeline daemons to coverage via _daemon_tp
#   - Added uptime gate (>5s) in _daemon_tp to catch crash loops
#   - Added log compaction
#   - Added self-heartbeat to mesh_memory each tick
#   - REMOVED defunct cron self-repair block.
#
# RUNS UNDER:
#   /home/workspace/zo_mesh/watchdog_daemon.py supervised by daemon_wrapper.sh.
#   Verify with:
#       pgrep -af watchdog_daemon
#       tail /home/workspace/logs/watchdog.log
#       zo_db_query "SELECT created_at FROM mesh_memory \
#                    WHERE agent_id='watchdog' AND memory_type='watchdog_tick' \
#                    ORDER BY created_at DESC LIMIT 5"
#
# v3.3: trust pipeline coverage + uptime gate + compaction + heartbeat
# v3.2: BuildWatcher health-check only (no restart)
# v3.1: WriteService DEAD sentinel
# v3.0: Original cron self-healer

LOGS=/home/workspace/logs; MESH=/home/workspace/zo_mesh; SENTINEL=/home/workspace/zo_sentinel
mkdir -p $LOGS
TS=$(date '+%Y-%m-%d %H:%M:%S'); HEALTHY=true; ACTIONS=()
RESTART_VERIFY=(); SVC_VERIFY=()
log() { echo "[$TS] $1"; }

TRUST_PIPELINE=(
    candidate_promoter_daemon.py
    candidate_npm_promoter.py
    registry_promoter_daemon.py
    fingerprint_runner_daemon_v3.py
    mcp_scanner.py
    signal_analyser.py
    trust_synthesiser.py
    threat_intel_ingestor.py
    risk_ranker.py
    attestation_engine.py
)

_svc() {
    local script=$1 port=$2 name=$3
    local code=$(curl -s -m 5 --connect-timeout 3 -o /dev/null -w "%{http_code}" http://127.0.0.1:$port/health 2>/dev/null)
    code=${code:-000}
    if [[ "$code" != "200" ]]; then
        log "$name down (code=$code) -- restarting"
        pkill -f "$script" 2>/dev/null || true
        rm -f $LOGS/write_service_DEAD
        sleep 2
        case $name in
            WriteService) nohup bash $MESH/write_service_wrapper.sh >> $LOGS/write_service.log 2>&1 & ;;
            InfRouter)    nohup python3 $MESH/inference_router_service.py >> $LOGS/inference_router.log 2>&1 & ;;
        esac
        sleep 3
        HEALTHY=false; ACTIONS+=("${name}_restart")
        SVC_VERIFY+=("$port|$name")
    fi
}

_bw_check() {
    local code=$(curl -s -m 5 --connect-timeout 3 -o /dev/null -w "%{http_code}" http://127.0.0.1:8795/health 2>/dev/null)
    code=${code:-000}
    if [[ "$code" != "200" ]]; then
        log "BuildWatcher :8795 down (code=$code) -- use ZoComputer Hosting Restart button"
        HEALTHY=false; ACTIONS+=("bw_down")
    fi
}

# v3.4: pgrep + pkill anchored to "python.*<script>" so wrapper bash
# processes don't count toward the duplicate count or get killed.
_daemon() {
    local script=$1 logfile=$2 name=$3 start_cmd=$4
    local count=$(pgrep -c -f "python.*$script" 2>/dev/null)
    count=${count:-0}
    if [[ "$count" -eq 0 ]]; then
        log "$name down -- restarting"
        eval "nohup $start_cmd >> $LOGS/$logfile 2>&1 &"
        HEALTHY=false; ACTIONS+=("${name}_restart")
        RESTART_VERIFY+=("python.*$script|$name")
    elif [[ "$count" -gt 1 ]]; then
        log "$name duplicates ($count) -- deduplicating"
        pkill -f "python.*$script" 2>/dev/null; sleep 2
        eval "nohup $start_cmd >> $LOGS/$logfile 2>&1 &"
        HEALTHY=false; ACTIONS+=("${name}_dedup")
        RESTART_VERIFY+=("python.*$script|$name")
    fi
}

_daemon_tp() {
    local script=$1
    local name="${script%.py}"
    local logfile="${name}.log"
    local pids=$(pgrep -f "python.*$script" 2>/dev/null)
    local count=$(echo "$pids" | grep -c .)
    count=${count:-0}

    if [[ "$count" -eq 0 ]]; then
        log "$name down -- restarting via daemon_wrapper"
        nohup bash $MESH/daemon_wrapper.sh "$name" "$SENTINEL/$script" >> "$LOGS/$logfile" 2>&1 &
        HEALTHY=false; ACTIONS+=("${name}_restart")
        RESTART_VERIFY+=("python.*$script|$name")
        return
    fi

    if [[ "$count" -gt 1 ]]; then
        log "$name duplicates ($count) -- deduplicating"
        pkill -f "python.*$script" 2>/dev/null; sleep 2
        nohup bash $MESH/daemon_wrapper.sh "$name" "$SENTINEL/$script" >> "$LOGS/$logfile" 2>&1 &
        HEALTHY=false; ACTIONS+=("${name}_dedup")
        RESTART_VERIFY+=("python.*$script|$name")
        return
    fi

    local pid=$(echo "$pids" | head -1)
    local etimes=$(ps -o etimes= -p "$pid" 2>/dev/null | tr -d ' ')
    etimes=${etimes:-0}

    if [[ "$etimes" -lt 5 ]]; then
        log "$name running but uptime ${etimes}s <5s -- possible crash loop, will recheck next tick"
        HEALTHY=false; ACTIONS+=("${name}_unstable")
    fi
}

# GH #3415 prevention 1 / FU-349: re-observe every restart ~10s later. A
# restart that yields no surviving process (or a service still non-200) is
# logged as a FAILURE, never as a repair. One sleep covers the whole batch,
# so a healthy tick costs nothing and a repair tick costs 10s.
_verify_restarts() {
    [[ ${#RESTART_VERIFY[@]} -eq 0 && ${#SVC_VERIFY[@]} -eq 0 ]] && return
    sleep 10
    local entry pat name port count code
    for entry in "${RESTART_VERIFY[@]}"; do
        pat="${entry%|*}"; name="${entry##*|}"
        count=$(pgrep -c -f "$pat" 2>/dev/null)
        count=${count:-0}
        if [[ "$count" -eq 0 ]]; then
            log "$name RESTART FAILED -- no surviving process 10s after relaunch (check its log for an import traceback)"
            HEALTHY=false; ACTIONS+=("${name}_restart_FAILED")
        fi
    done
    for entry in "${SVC_VERIFY[@]}"; do
        port="${entry%|*}"; name="${entry##*|}"
        code=$(curl -s -m 5 --connect-timeout 3 -o /dev/null -w "%{http_code}" http://127.0.0.1:$port/health 2>/dev/null)
        code=${code:-000}
        if [[ "$code" != "200" ]]; then
            log "$name RESTART FAILED -- /health $code 10s after relaunch"
            HEALTHY=false; ACTIONS+=("${name}_restart_FAILED")
        fi
    done
}

_compact_logs() {
    local max_bytes=$((5 * 1024 * 1024))   # 5 MB
    local keep_lines=10000
    local count=0
    for f in $LOGS/*.log; do
        [[ -f "$f" ]] || continue
        local size=$(stat -c %s "$f" 2>/dev/null)
        size=${size:-0}
        if [[ "$size" -gt "$max_bytes" ]]; then
            tail -n "$keep_lines" "$f" > "${f}.tmp" 2>/dev/null && mv "${f}.tmp" "$f"
            count=$((count + 1))
        fi
    done
    if [[ "$count" -gt 0 ]]; then
        log "compacted $count log(s) >5MB to last $keep_lines lines"
        ACTIONS+=("compacted_${count}_logs")
    fi
}

_self_heartbeat() {
    local actions_json
    # Join ACTIONS array elements with commas (POSIX compatible)
    local joined=""
    for act in "${ACTIONS[@]}"; do
        [[ -n "$joined" ]] && joined="$joined,$act" || joined="$act"
    done
    local payload="{\"ts\":\"$TS\",\"healthy\":$HEALTHY,\"actions\":\"$joined\"}"
    curl -s -m 5 -X POST http://127.0.0.1:8772/write \
        -H 'Content-Type: application/json' \
        -d "{\"table\":\"mesh_memory\",\"rows\":[{\"agent_id\":\"watchdog\",\"memory_type\":\"watchdog_tick\",\"content\":$(echo "$payload" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))'),\"importance\":0.4}]}" \
        > /dev/null 2>&1 || true
}

# === MAIN ==================================================================

[[ -f "$LOGS/write_service_DEAD" ]] && { log "WriteService DEAD sentinel -- forcing restart"; pkill -f 'write_service' 2>/dev/null; sleep 2; rm -f $LOGS/write_service_DEAD; }

_svc write_service_wrapper.sh   8772 WriteService
_svc inference_router_service.py 8773 InfRouter
_bw_check

_daemon pipeline_bridge.py       pipeline_bridge.log      PipelineBridge  "python3 $MESH/pipeline_bridge.py"
_daemon t2_consumer_agents.py    t2_consumer.log          T2Consumer      "python3 $MESH/t2_consumer_agents.py"
_daemon anti_entropy_daemon.py   anti_entropy_daemon.log  AntiEntropy     "python3 $MESH/anti_entropy_daemon.py"
_daemon mesh_self_diagnostics.py self_diagnostics.log     SelfDiag        "python3 $MESH/mesh_self_diagnostics.py"
_daemon data_velocity_engine.py  data_velocity.log        DataVelocity    "python3 $MESH/data_velocity_engine.py"
_daemon wisdom_synthesiser.py    wisdom_synthesiser.log   Wisdom          "python3 $MESH/wisdom_synthesiser.py"
_daemon run_manager.py           manager.log              Manager         "python3 $MESH/run_manager.py daemon"
_daemon goose_runner.py          goose_runner.log         GooseRunner     "env ZO_ESCALATE=1 python3 $SENTINEL/goose_runner.py"
_daemon proposed_to_pending_promoter proposed_to_pending_promoter.log PromoterP2P "bash -c 'cd $SENTINEL && exec python3 -m zo_sentinel.promoters.proposed_to_pending_promoter'"

# v3.8: build->publish pipeline janitor -- ghost .done sweep + heal the
# publisher/ingestor/governor `python3 -m` loops when they crash-loop on the
# module-shadow import bug. Self-contained in tools/sentinel_janitor.sh so this
# precious file stays minimal and go.sh stays out of it entirely. Idempotent and
# non-thrashing (only acts on a broken/absent loop), so safe every tick.
bash $SENTINEL/tools/sentinel_janitor.sh >> $LOGS/sentinel_janitor.log 2>&1 || true

for sc in "${TRUST_PIPELINE[@]}"; do
    _daemon_tp "$sc"
done

# v3.4: pgrep pattern includes "python" prefix to skip wrapper scripts.
WD=$(pgrep -c -f 'python.*run.py --daemon' 2>/dev/null)
WD=${WD:-0}
if [[ "$WD" -gt 2 ]]; then
    log "WorldAgent duplicates ($WD) -- deduplicating"
    pkill -9 -f 'python.*run.py --daemon' 2>/dev/null; sleep 3
    cd /home/workspace/world_agent && nohup python run.py --daemon >> $LOGS/world_agent.log 2>&1 &
    HEALTHY=false; ACTIONS+=("wa_dedup")
    RESTART_VERIFY+=("python.*run.py --daemon|WorldAgent")
elif [[ "$WD" -eq 0 ]]; then
    log "WorldAgent down -- restarting"
    cd /home/workspace/world_agent && nohup python run.py --daemon >> $LOGS/world_agent.log 2>&1 &
    HEALTHY=false; ACTIONS+=("wa_restart")
    RESTART_VERIFY+=("python.*run.py --daemon|WorldAgent")
fi

IE=$(pgrep -c -f 'python.*intent_engine_daemon.py' 2>/dev/null)
IE=${IE:-0}
[[ "$IE" -gt 1 ]] && { log "IntentEngine dedup ($IE)"; pkill -f 'python.*intent_engine_daemon.py' 2>/dev/null; sleep 2
    nohup python3 /home/workspace/Skills/childofintent-intent-engine/scripts/intent_engine_daemon.py >> $LOGS/intent_engine.log 2>&1 &
    HEALTHY=false; ACTIONS+=("ie_dedup")
    RESTART_VERIFY+=("python.*intent_engine_daemon.py|IntentEngine"); }

BYOK=$(grep -rl 'model_name.*byok:' /home/workspace/Skills/ --include='*.ts' --include='*.py' 2>/dev/null | grep -v '.bak.' | head -5)
[[ -n "$BYOK" ]] && { log "BYOK ALERT: $BYOK"; HEALTHY=false; ACTIONS+=("byok_alert"); }

_verify_restarts
_compact_logs
_self_heartbeat

$HEALTHY && log "all healthy" || log "repaired: ${ACTIONS[*]}"
echo ""
