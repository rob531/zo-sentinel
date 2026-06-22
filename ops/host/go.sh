#!/usr/bin/env bash
# go.sh v2.9.2 -- ZOMesh SOA recovery
#
# v2.9.2 (2026-06-07): STAGGERED STARTUP. The 3b gate ensures the writer is UP
#   before the herd; v2.9.2 ensures the herd doesn't all hit it AT ONCE once it
#   is up. Daemons now start in writer-gated waves: wait_writer_calm() pauses
#   between waves and inside the 13-daemon trust-pipeline loop (every 3rd daemon)
#   until :8772 answers /health quickly, with a hard cap (WAVE_GATE_MAX) so boot
#   always completes. Tunable: STAGGER_SECS / WAVE_GATE_SECS / WAVE_GATE_MAX.
#
# RESYNCED 2026-06-07 from the live host (/home/workspace/zo_mesh/go.sh) to
# mirror the DuckDB-contention hardening applied during the 2026-06-05 incident
# firefight (the 651-lock storm) that never made it back into source control:
#   - Section 1 (v2.9.1): aggressive DuckDB lock cleanup -- fuser the writer DB,
#     kill -9 any straggler holding the lock, rm stale .wal/.lock so the new
#     write_service starts clean (fixes wrapper-restart stale-lock races).
#   - Section 3b (PR #75): WriteService readiness gate -- wait up to 90s for a
#     healthy :8772 BEFORE starting the ~40-daemon herd, so they don't pile onto
#     a not-ready single writer.
#   - curl health probes get -m5/-m3 timeouts so a hung probe can't stall boot.
# NOTE: the live host go.sh has additional, unrelated drift not pulled in here
#   (builder retirement at 12, ladder_shim_with_keys.sh, the proposed->pending
#   promoter at 12.5c). Those are separate workstreams -- sync them deliberately.
#
# 2026-05-28 patches:
#   - Section 0.0 Tailscale (userspace networking) added at the top, before
#     key_hydrator. Modal containers don't expose /dev/net/tun, so tailscaled
#     must run in --tun=userspace-networking mode. Without this the
#     key_hydrator's Tower-vault request times out and the whole stack
#     starves of API keys.
#   - Section 18 bootstrap wait gate bumped 60s -> 120s to match the new
#     wait_for_ws default in full_schema_bootstrap.py. The python script
#     also has its own 120s gate so we fall through to it even on timeout
#     rather than skip+abort.
# Backup of pre-patch script: go.sh.bak
#
# CHANGELOG vs v2.8:
#   - TRUST_PIPELINE extended to include breadth daemons (2026-04-29).
#   - MESH_DAEMONS (NEW) section 12.12: covers trigger_watcher.
#   - Section 1's pkill loop extended to cover MESH_DAEMONS.
#
# CHANGELOG vs v2.7:
#   - Section 0 (NEW): cold-boot detection.
#   - --verify flag (NEW), --help flag (NEW), arg parsing at the top.
#   - Section 12.11 (NEW): trust pipeline cluster (10 daemons).
#
# WHAT IS UNCHANGED (deliberate -- load-bearing):
#   - Wrapper-script + nohup pattern, NOT supervisord-user.conf.
#   - BuildWatcher owned by ZoComputer Hosting (do-not-kill).
#   - Watchdog cron (section 17) and schema bootstrap (section 18) unchanged.

set -e
GRN='\033[0;32m'; YLW='\033[0;33m'; BOLD='\033[1m'; NC='\033[0m'
ok(){  echo -e "  ${GRN}OK${NC} $1"; }
warn(){ echo -e "  ${YLW}!!${NC} $1"; }
hdr(){  echo -e "\n${BOLD}=== $1 ===${NC}"; }
MESH=/home/workspace/zo_mesh
SENTINEL=/home/workspace/zo_sentinel
LOGS=/home/workspace/logs
# Launcher version -- auto-read from this file's own header line (# go.sh vX.Y).
# Used by the boot marker (end of file) so "which go.sh is live?" is one query.
# Hardcoded (was auto-read from "$0", which is empty when `zm go` SOURCES this
# script -> the marker stamped a blank "v." on 2026-06-10). Bump this line when the
# header version above changes.
GO_SH_VERSION="2.9.2"
SUPCTL="supervisorctl -c /etc/zo/supervisord-user.conf"
mkdir -p $LOGS

# -----------------------------------------------------------------------------
# Staggered startup knobs + writer-calm gate (NEW v2.9.2)
# -----------------------------------------------------------------------------
# The ~40 daemons below mostly write to the single DuckDB writer (:8772). The 3b
# gate ensures the writer is UP before the herd; these knobs ensure the herd does
# not all hit it AT ONCE once it's up -- the boot-herd lock storm (651 conflicts
# on 2026-06-05). Daemons start in waves; wait_writer_calm() pauses between waves
# (and inside the trust-pipeline loop) until the writer answers /health quickly,
# with a hard cap so boot always completes. All knobs are env-overridable.
STAGGER_SECS="${STAGGER_SECS:-4}"      # per-daemon stagger inside a wave
WAVE_GATE_SECS="${WAVE_GATE_SECS:-3}"  # poll interval while waiting for calm
WAVE_GATE_MAX="${WAVE_GATE_MAX:-20}"   # max polls (~60s) before proceeding anyway
wait_writer_calm() {
  # Return once :8772 answers /health in under ~1s (writer not saturated), or
  # after WAVE_GATE_MAX polls. Never blocks boot indefinitely.
  local i t0 t1 code
  for i in $(seq 1 "$WAVE_GATE_MAX"); do
    t0=$(date +%s%3N)
    code=$(curl -m3 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8772/health 2>/dev/null || echo 000)
    t1=$(date +%s%3N)
    if [[ "$code" == "200" && $((t1 - t0)) -lt 1000 ]]; then return 0; fi
    sleep "$WAVE_GATE_SECS"
  done
  warn "writer still busy after $((WAVE_GATE_MAX * WAVE_GATE_SECS))s gate -- proceeding anyway"
  return 0
}

# -----------------------------------------------------------------------------
# 0.0 Tailscale (userspace networking)
#
# Modal containers don't expose /dev/net/tun, so tailscaled cannot create the
# tailscale0 kernel interface (operation-not-permitted from tstun.New). Run
# in --tun=userspace-networking mode instead. A SOCKS5/HTTP proxy on :1055
# lets in-container apps reach Tailscale peers (e.g. key_hydrator's Tower-
# vault request).
#
# Idempotent: skips startup if tailscaled is already running, skips auth if
# already logged in. TAILSCALE_AUTH_KEY must be sourced from /etc/zo/env or
# .zo_env for first-time auth; subsequent boots reuse persisted state.
#
# Failures here are non-fatal (warn, not abort).
# -----------------------------------------------------------------------------
hdr "0.0 Tailscale (userspace networking)"
set +e  # tailscale start failures must not abort the rest of go.sh
if ! pgrep -af tailscaled > /dev/null 2>&1; then
    nohup sudo tailscaled \
        --tun=userspace-networking \
        --socks5-server=localhost:1055 \
        --outbound-http-proxy-listen=localhost:1055 \
        --state=/var/lib/tailscale/tailscaled.state \
        >> $LOGS/tailscaled.log 2>&1 &
    sleep 3
    pgrep -af tailscaled > /dev/null && ok "tailscaled (userspace) started" || warn "tailscaled failed to start (see $LOGS/tailscaled.log)"
else
    ok "tailscaled already running"
fi

if ! sudo tailscale status > /dev/null 2>&1; then
    if [[ -n "$TAILSCALE_AUTH_KEY" ]]; then
        sudo tailscale up --auth-key="$TAILSCALE_AUTH_KEY" >> $LOGS/tailscaled.log 2>&1 \
            && ok "tailscale authed" \
            || warn "tailscale up failed (check $LOGS/tailscaled.log)"
    else
        warn "TAILSCALE_AUTH_KEY not set; tailscale not authed (peers unreachable until manual 'sudo tailscale up')"
    fi
else
    ok "tailscale already authed"
fi
set -e

# Key vault hydration -- Tower AgentVault -> ZoComputer env
# See /home/workspace/zo_mesh/SECRETS.md for full key registry
python3 $MESH/key_hydrator.py 2>&1 | tee -a $LOGS/key_hydrator.log | tail -3


TRUST_PIPELINE=(
    candidate_promoter_daemon.py
    candidate_npm_promoter.py
    candidate_github_promoter.py
    discovery_npm_paginator.py
    discovery_github_paginator.py
    registry_promoter_daemon.py
    fingerprint_runner_daemon_v3.py
    mcp_scanner.py
    signal_analyser.py
    trust_synthesiser.py
    threat_intel_ingestor.py
    risk_ranker.py
    attestation_engine.py
)

MESH_DAEMONS=(
    trigger_watcher.py
)


# === SUPERVISORD_FILTER_v1 ===
SUPERVISORD_KEEPALIVE=0
_NEW_ARGS=()
for _a in "$@"; do
  case "$_a" in
    "&&"|sleep|infinity) SUPERVISORD_KEEPALIVE=1 ;;
    *) _NEW_ARGS+=("$_a") ;;
  esac
done
set -- "${_NEW_ARGS[@]}"
unset _NEW_ARGS _a
# === END SUPERVISORD_FILTER_v1 ===

MODE="recover"
for arg in "$@"; do
  case "$arg" in
    --verify) MODE="verify" ;;
    --help|-h)
      cat <<EOF
Usage: zm go [--verify]

  (no flag)   Full recovery: ~90 seconds.
  --verify    SUMMARY only. ~3 seconds.
  --help      This message.
EOF
      exit 0 ;;
    *) echo "unknown argument: $arg (try --help)" >&2; exit 2 ;;
  esac
done

if [[ "$MODE" == "verify" ]]; then
  hdr "VERIFY (no restarts; SUMMARY only)"
  echo "  :8772 WriteService:      $(curl -m5 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8772/health 2>/dev/null)"
  echo "  :8773 InferenceRouter:   $(curl -m5 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8773/health 2>/dev/null)"
  echo "  :8780 ApprovalWorkflow:  $(curl -m5 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8780/health 2>/dev/null)"
  echo "  :8781 RegistryAPI:       $(curl -m5 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8781/health 2>/dev/null)"
  echo "  :8790 SentinelUI:        $(curl -m5 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8790/health 2>/dev/null)"
  echo "  :8795 BuildWatcher:      $(curl -m5 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8795/health 2>/dev/null)"
  echo "  tailscaled procs:        $(pgrep -af tailscaled 2>/dev/null | wc -l | tr -d ' ')"
  PYCOUNT=$(pgrep -af python3 2>/dev/null | wc -l | tr -d ' ')
  echo "  python3 procs running:   $PYCOUNT"
  echo ""
  TP_RUNNING=0
  for sc in "${TRUST_PIPELINE[@]}"; do pgrep -f "$sc" >/dev/null 2>&1 && TP_RUNNING=$((TP_RUNNING + 1)); done
  MD_RUNNING=0
  for sc in "${MESH_DAEMONS[@]}"; do pgrep -f "$sc" >/dev/null 2>&1 && MD_RUNNING=$((MD_RUNNING + 1)); done
  echo "  trust pipeline up:       ${TP_RUNNING}/${#TRUST_PIPELINE[@]}"
  echo "  mesh daemons up:         ${MD_RUNNING}/${#MESH_DAEMONS[@]}"
  exit 0
fi

if [[ -f "$MESH/.zo_env" ]]; then
    set -a; source "$MESH/.zo_env"; set +a
    ok "API keys loaded -- ZO_API_KEY: $([[ -n "$ZO_API_KEY" ]] && echo SET || echo NOT_SET) | MINIMAX: $([[ -n "$MINIMAX_API_KEY" ]] && echo SET || echo NOT_SET)"
fi
[[ -f /etc/zo/env ]] && { set -a; source /etc/zo/env; set +a; } || true

hdr "0. Boot mode detection"
WORKSPACE_DAEMONS=$(pgrep -af 'python3 /home/workspace/zo_(mesh|sentinel)/' 2>/dev/null | wc -l | tr -d ' ')
if [[ "$WORKSPACE_DAEMONS" -le 1 ]]; then
  ok "Cold boot ($WORKSPACE_DAEMONS workspace daemons running)"
else
  ok "Hot recovery ($WORKSPACE_DAEMONS workspace daemons currently running)"
fi

hdr "1. Kill stale daemons + duplicates"
$SUPCTL stop zo_sentinel_builder 2>/dev/null && warn "stopped zo_sentinel_builder" || true
sleep 1
for proc in write_service.py inference_router_service.py run_manager.py \
            pipeline_bridge.py t2_consumer_agents.py anti_entropy_daemon.py \
            mesh_self_diagnostics.py data_velocity_engine.py wisdom_synthesiser.py \
            world_article_feeder.py zo_sentinel_builder.py goose_runner.py \
            sentinel_directive_generator.py sentinel_directive_generator_goose.py gate_scheduler.py \
            liveness_probe.py loop_watch.py graph_refresh.py signal_bridge.py ecosystems_metadata_fetcher.py \
            registry_api.py approval_workflow.py \
            zo_sentinel.ingestor zo_sentinel.publisher \
            "${TRUST_PIPELINE[@]}" \
            "${MESH_DAEMONS[@]}"; do
    pkill -f $proc 2>/dev/null && warn "killed $proc" || true
done
# NOTE: do NOT kill build_watcher_api.py -- owned by ZoComputer Hosting
# NOTE: do NOT kill tailscaled -- owns the userspace tunnel
pkill -f 'write_service_wrapper' 2>/dev/null || true
pkill -f 'daemon_wrapper.sh' 2>/dev/null || true
pkill -f 'python run.py --daemon'  2>/dev/null && warn "killed run.py daemon(s)" || true
pkill -f 'python3 run.py --daemon' 2>/dev/null || true
rm -f $LOGS/write_service_DEAD
IE=$(pgrep -f 'intent_engine_daemon.py' 2>/dev/null | wc -l)
[[ "$IE" -gt 1 ]] && pkill -f 'intent_engine_daemon.py' 2>/dev/null && warn "killed $IE intent_engine duplicates" || true
for sc in "${TRUST_PIPELINE[@]}" "${MESH_DAEMONS[@]}"; do
  NAME="${sc%.py}"
  rm -f "$LOGS/${NAME}.lock" 2>/dev/null || true
done
sleep 3
# Aggressive DuckDB lock cleanup (NEW v2.9.1)
# After pkill, some write_service.py processes may still hold the DuckDB lock
# (mid-transaction, zombie, or wrapper-restart race). Force-kill any straggler
# and remove stale lock files so the new writer can start clean.
DB_PATH=/home/workspace/Datasets/zo-mesh/data.duckdb
for _ in $(seq 1 3); do
  STALE_PID=$(fuser "$DB_PATH" 2>/dev/null | tr -d ' ')
  if [[ -n "$STALE_PID" ]]; then
    warn "DuckDB still locked by PID(s) $STALE_PID -- force killing"
    kill -9 $STALE_PID 2>/dev/null || true
    sleep 2
  fi
done
# Remove stale WAL and lock files so DuckDB starts fresh
rm -f "${DB_PATH}.wal" "${DB_PATH}.lock" 2>/dev/null || true
# Verify unlock
if fuser "$DB_PATH" 2>/dev/null | grep -q .; then
  warn "DuckDB LOCK STILL HELD after cleanup -- boot may fail"
else
  ok "DuckDB lock released"
fi

hdr "2. Ollama"
curl -m5 -s http://localhost:11434/api/tags >/dev/null 2>&1 \
    && ok "already running" \
    || { nohup ollama serve >> $LOGS/ollama.log 2>&1 & sleep 4 && ok "started"; }

hdr "3. WriteService :8772"
chmod +x $MESH/write_service_wrapper.sh
nohup bash $MESH/write_service_wrapper.sh >> $LOGS/write_service.log 2>&1 & sleep 4
CODE=$(curl -m5 -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8772/health 2>/dev/null || echo 000)
[[ "$CODE" == "200" ]] && ok ":8772 up" || warn ":8772 not responding ($CODE)"

hdr "3b. WriteService readiness gate (gate the herd on a ready writer)"
# The ~40 daemons below mostly write to :8772 (the single DuckDB writer). Starting
# them before write_service is healthy piles a thundering herd onto a not-ready
# writer -> DuckDB lock contention, timeouts, the recurring instability + slow
# boots. Wait up to 90s for :8772 HERE, before the herd (the old readiness wait
# lived at section 18 -- after everything had already started hammering it).
WS_GATE=0
for i in $(seq 1 30); do
    if [[ "$(curl -m3 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8772/health 2>/dev/null)" == "200" ]]; then WS_GATE=1; break; fi
    sleep 3
done
[[ "$WS_GATE" == "1" ]] && ok ":8772 ready -- starting dependent daemons" || warn ":8772 NOT ready after 90s -- daemons may contend"

hdr "4. InferenceRouter :8773"
IR=$(curl -m5 -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8773/health 2>/dev/null || echo 000)
if [[ "$IR" == "200" ]]; then
    ok ":8773 already running"
else
    nohup python3 $MESH/inference_router_service.py >> $LOGS/inference_router.log 2>&1 & sleep 3
    CODE=$(curl -m5 -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8773/health 2>/dev/null || echo 000)
    [[ "$CODE" == "200" ]] && ok ":8773 started" || warn ":8773 failed ($CODE)"
fi

hdr "5. ManagerAgent (ZO_API_KEY: $([[ -n "$ZO_API_KEY" ]] && echo SET || echo NOT_SET))"
nohup python3 $MESH/run_manager.py daemon >> $LOGS/manager.log 2>&1 & sleep 2
MA_PID=$(pgrep -f 'run_manager.py' 2>/dev/null | head -1)
[[ -n "$MA_PID" ]] && ok "ManagerAgent PID $MA_PID" || warn "ManagerAgent failed"

hdr "6. Pipeline Bridge"
nohup python3 $MESH/pipeline_bridge.py >> $LOGS/pipeline_bridge.log 2>&1 & sleep 2
PB=$(pgrep -f 'pipeline_bridge.py' 2>/dev/null | head -1)
[[ -n "$PB" ]] && ok "PipelineBridge PID $PB" || warn "PipelineBridge failed"

hdr "7. T2 Consumer Agents"
nohup python3 $MESH/t2_consumer_agents.py >> $LOGS/t2_consumer.log 2>&1 & sleep 2
T2=$(pgrep -f 't2_consumer_agents.py' 2>/dev/null | head -1)
[[ -n "$T2" ]] && ok "T2Consumer PID $T2" || warn "T2Consumer failed"

hdr "8. Anti-Entropy Daemon"
nohup python3 $MESH/anti_entropy_daemon.py >> $LOGS/anti_entropy_daemon.log 2>&1 & sleep 2
AE=$(pgrep -f 'anti_entropy_daemon.py' 2>/dev/null | head -1)
[[ -n "$AE" ]] && ok "AntiEntropyDaemon PID $AE" || warn "AntiEntropyDaemon failed"

hdr "9. Mesh Self-Diagnostics"
nohup python3 $MESH/mesh_self_diagnostics.py >> $LOGS/self_diagnostics.log 2>&1 & sleep 2
SD=$(pgrep -f 'mesh_self_diagnostics.py' 2>/dev/null | head -1)
[[ -n "$SD" ]] && ok "SelfDiagnostics PID $SD" || warn "SelfDiagnostics failed"

hdr "10. Data Velocity Engine"
nohup python3 $MESH/data_velocity_engine.py >> $LOGS/data_velocity.log 2>&1 & sleep 2
DV=$(pgrep -f 'data_velocity_engine.py' 2>/dev/null | head -1)
[[ -n "$DV" ]] && ok "DataVelocity PID $DV" || warn "DataVelocity failed"

hdr "11. Wisdom Synthesiser"
nohup python3 $MESH/wisdom_synthesiser.py >> $LOGS/wisdom_synthesiser.log 2>&1 & sleep 2
WS9=$(pgrep -f 'wisdom_synthesiser.py' 2>/dev/null | head -1)
[[ -n "$WS9" ]] && ok "WisdomSynthesiser PID $WS9" || warn "WisdomSynthesiser failed"

hdr "12. ZO-SENTINEL Builder (nohup with env)"
nohup bash $MESH/daemon_wrapper.sh zo_sentinel_builder $MESH/zo_sentinel_builder.py >> $LOGS/zo_sentinel_builder.log 2>&1 &
sleep 3
ZSB=$(pgrep -f 'zo_sentinel_builder.py' 2>/dev/null | head -1)
[[ -n "$ZSB" ]] && ok "SentinelBuilder PID $ZSB" || warn "SentinelBuilder failed"

hdr "12.4 Ladder Shim :8796"
LS=$(curl -m5 -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8796/health 2>/dev/null || echo 000)
if [[ "$LS" == "200" ]]; then
    ok ":8796 ladder_shim already running"
else
    nohup bash $MESH/daemon_wrapper.sh ladder_shim $SENTINEL/ladder_shim.py >> $LOGS/ladder_shim.log 2>&1 & sleep 3
    LS2=$(curl -m5 -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8796/health 2>/dev/null || echo 000)
    [[ "$LS2" == "200" ]] && ok ":8796 ladder_shim started" || warn ":8796 ladder_shim failed ($LS2)"
fi

hdr "12.4b Goose Runner"
nohup bash $MESH/daemon_wrapper.sh goose_runner $SENTINEL/goose_runner.py >> $LOGS/goose_runner.log 2>&1 &
sleep 2
GR=$(pgrep -f 'goose_runner.py' 2>/dev/null | head -1)
[[ -n "$GR" ]] && ok "GooseRunner PID $GR" || warn "GooseRunner failed"

hdr "12.5 Sentinel Directive Generator"
nohup bash $MESH/daemon_wrapper.sh sentinel_directive_generator $SENTINEL/sentinel_directive_generator.py >> $LOGS/sentinel_sentinel_directive_generator.log 2>&1 &
sleep 2
SDG=$(pgrep -f 'sentinel_directive_generator.py' 2>/dev/null | head -1)
[[ -n "$SDG" ]] && ok "DirectiveGenerator PID $SDG" || warn "DirectiveGenerator failed"

hdr "12.5b Sentinel Directive Generator (Goose -- Phase 0b sibling)"
nohup bash $MESH/daemon_wrapper.sh sentinel_directive_generator_goose $SENTINEL/sentinel_directive_generator_goose.py >> $LOGS/sentinel_directive_generator_goose.log 2>&1 &
sleep 2
SDGG=$(pgrep -f 'sentinel_directive_generator_goose.py' 2>/dev/null | head -1)
[[ -n "$SDGG" ]] && ok "DirectiveGeneratorGoose PID $SDGG" || warn "DirectiveGeneratorGoose failed"

hdr "12.6 Gate Scheduler"
nohup bash $MESH/daemon_wrapper.sh gate_scheduler $SENTINEL/gate_scheduler.py >> $LOGS/gate_scheduler.log 2>&1 &
sleep 2
GSC=$(pgrep -f 'gate_scheduler.py' 2>/dev/null | head -1)
[[ -n "$GSC" ]] && ok "GateScheduler PID $GSC" || warn "GateScheduler failed"

hdr "12.6b Code-Artifact Ingestion Trio (gated -- DORMANT until latched)"
# Consume the build_artifact rows the live goose build now emits (PR #18). All
# three are SAFE to always run: the ingestor + publisher NO-OP until their latch
# file exists, and the governor only flips the ingestor latch once builds prove
# green AND agree with gate_8. To activate:
#   ingestor:  touch $SENTINEL/.ingestor_enabled      (or let the governor do it)
#   publisher: export PR_PUBLISHER_CLONE_DIR=<clone>; touch $SENTINEL/.pr_publisher_enabled
# python -m needs the package on PYTHONPATH ($SENTINEL holds the zo_sentinel pkg).
# Ingestor -- continuous (built-in --interval loop); dormant w/o .ingestor_enabled
nohup env PYTHONPATH="$SENTINEL" python3 -m zo_sentinel.ingestor run --interval 300 \
    >> $LOGS/artifact_ingestor.log 2>&1 &
sleep 1
ING=$(pgrep -f 'zo_sentinel.ingestor run' 2>/dev/null | head -1)
[[ -n "$ING" ]] && ok "ArtifactIngestor PID $ING (dormant until .ingestor_enabled)" || warn "ArtifactIngestor failed"
# Governor -- `govern` is one-shot; poll-loop it. Auto-activates ingestor when ready.
nohup env PYTHONPATH="$SENTINEL" bash -c \
    'while true; do python3 -m zo_sentinel.ingestor govern; sleep 600; done' \
    >> $LOGS/activation_governor.log 2>&1 &
sleep 1
GOV=$(pgrep -f 'zo_sentinel.ingestor govern' 2>/dev/null | head -1)
[[ -n "$GOV" ]] && ok "ActivationGovernor PID $GOV" || warn "ActivationGovernor failed"
# Publisher -- `run-once` is one-shot; poll-loop it. Dormant w/o .pr_publisher_enabled.
nohup env PYTHONPATH="$SENTINEL" bash -c \
    'while true; do python3 -m zo_sentinel.publisher run-once; sleep 600; done' \
    >> $LOGS/pr_publisher.log 2>&1 &
sleep 1
PUB=$(pgrep -f 'zo_sentinel.publisher run-once' 2>/dev/null | head -1)
[[ -n "$PUB" ]] && ok "PRPublisher PID $PUB (dormant until .pr_publisher_enabled)" || warn "PRPublisher failed"

hdr "12.7 Liveness Probe"
nohup bash $MESH/daemon_wrapper.sh liveness_probe $MESH/liveness_probe.py >> $LOGS/liveness_probe.log 2>&1 &
sleep 2
LP=$(pgrep -f 'liveness_probe.py' 2>/dev/null | head -1)
[[ -n "$LP" ]] && ok "LivenessProbe PID $LP" || warn "LivenessProbe failed"

hdr "12.8 Signal Bridge"
nohup bash $MESH/daemon_wrapper.sh signal_bridge $SENTINEL/signal_bridge.py >> $LOGS/signal_bridge.log 2>&1 &
sleep 2
SB=$(pgrep -f 'signal_bridge.py' 2>/dev/null | head -1)
[[ -n "$SB" ]] && ok "SignalBridge PID $SB" || warn "SignalBridge failed"

hdr "12.9 Ecosystems Metadata Fetcher"
nohup bash $MESH/daemon_wrapper.sh ecosystems_metadata_fetcher $SENTINEL/ecosystems_metadata_fetcher.py >> $LOGS/ecosystems_metadata_fetcher.log 2>&1 &
sleep 2
EMF=$(pgrep -f 'ecosystems_metadata_fetcher.py' 2>/dev/null | head -1)
[[ -n "$EMF" ]] && ok "EcosystemsFetcher PID $EMF" || warn "EcosystemsFetcher failed"

hdr "12.10 Sentinel APIs (registry_api :8781 + approval_workflow :8780)"
RA=$(curl -m5 -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8781/health 2>/dev/null || echo 000)
if [[ "$RA" == "200" ]]; then
    ok ":8781 registry_api already running"
else
    nohup python3 $SENTINEL/registry_api.py >> $LOGS/sentinel_registry_api.log 2>&1 & sleep 3
    RA2=$(curl -m5 -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8781/health 2>/dev/null || echo 000)
    [[ "$RA2" == "200" ]] && ok ":8781 registry_api started" || warn ":8781 registry_api failed ($RA2)"
fi
AW=$(curl -m5 -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8780/health 2>/dev/null || echo 000)
if [[ "$AW" == "200" ]]; then
    ok ":8780 approval_workflow already running"
else
    nohup python3 $SENTINEL/approval_workflow.py >> $LOGS/sentinel_approval.log 2>&1 & sleep 3
    AW2=$(curl -m5 -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8780/health 2>/dev/null || echo 000)
    [[ "$AW2" == "200" ]] && ok ":8780 approval_workflow started" || warn ":8780 approval_workflow failed ($AW2)"
fi

hdr "12.10b Sentinel APIs -- revived 2026-06-10 (forensic_v2 :8779, bulk_assess :8784, search :8782, manual_override :8776)"
# ROOT CAUSE of the dark API services: built but never added to the launcher.
# These four are real, distinct features -> wired in here. RETIRED (deliberately
# NOT launched): registry_api_v2 (superseded by registry_api :8781) and
# dashboard_api (superseded by ui_server /api/dashboard :8790) -- archive, not revive.
# manual_override_api also needs PR #141 (syntax fix) merged or it crashes on import.
# Uses a "!=000" listening check (not strict /health==200) so services without a
# /health route still register as up.
# WRITER-GATED: wait for :8772 to be calm before launching, so these (each does a
# startup heartbeat + bulk/forensic create-tables) don't add to the boot herd that
# caused the 651-conflict lock storm. They are idle-after-startup APIs (search +
# manual_override heartbeat once; bulk + forensic every 30s), not continuous
# writers -- but gate anyway, and keep the per-service `sleep` stagger below.
wait_writer_calm
for _svc in "forensic_detail_api_v2:8779:forensic_detail" \
            "bulk_assess_api:8784:bulk_assess" \
            "search_api:8782:search_api" \
            "manual_override_api:8776:manual_override"; do
    _file="${_svc%%:*}"; _rest="${_svc#*:}"; _port="${_rest%%:*}"; _log="${_rest#*:}"
    # NB: curl -w "%{http_code}" ALREADY prints 000 on a dead port. Do NOT add
    # `|| echo 000` -- on a dead port curl prints 000 AND exits non-zero, so the
    # `||` appends a 2nd 000 -> "000000" -> "!= 000" is TRUE -> falsely "already
    # running" -> the launch is SKIPPED (the 2026-06-10 no-log-files bug). Use
    # `:-000` only to cover the rare empty-output case.
    _h=$(curl -m5 -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$_port/health 2>/dev/null); _h=${_h:-000}
    if [[ "$_h" != "000" ]]; then
        ok ":$_port $_file already running"
    else
        nohup python3 $SENTINEL/$_file.py >> $LOGS/sentinel_$_log.log 2>&1 & sleep 2
        _h2=$(curl -m5 -s -o /dev/null -w "%{http_code}" http://127.0.0.1:$_port/ 2>/dev/null); _h2=${_h2:-000}
        [[ "$_h2" != "000" ]] && ok ":$_port $_file started" \
            || warn ":$_port $_file not listening -- check $LOGS/sentinel_$_log.log"
    fi
done

hdr "12.11 Trust Pipeline Cluster (13 daemons)"
# Wave boundary: let the core-services wave (4-12.10) drain on the writer before
# releasing the 13 heavy continuous writers below.
wait_writer_calm
TP_N=0
for sc in "${TRUST_PIPELINE[@]}"; do
    NAME="${sc%.py}"
    nohup bash $MESH/daemon_wrapper.sh "$NAME" "$SENTINEL/$sc" >> "$LOGS/${NAME}.log" 2>&1 &
    TP_N=$((TP_N + 1))
    # Stagger each daemon; re-gate on writer calm every 3rd so 13 writers don't
    # pile onto :8772 at once (was a flat `sleep 1`).
    if [[ $((TP_N % 3)) -eq 0 ]]; then wait_writer_calm; else sleep "$STAGGER_SECS"; fi
    PID=$(pgrep -f "$sc" 2>/dev/null | head -1)
    [[ -n "$PID" ]] && ok "$NAME PID $PID" || warn "$NAME failed to start"
done

hdr "12.12 Mesh Daemons"
for sc in "${MESH_DAEMONS[@]}"; do
    NAME="${sc%.py}"
    nohup bash $MESH/daemon_wrapper.sh "$NAME" "$MESH/$sc" >> "$LOGS/${NAME}.log" 2>&1 &
    sleep 1
    PID=$(pgrep -f "$sc" 2>/dev/null | head -1)
    [[ -n "$PID" ]] && ok "$NAME PID $PID" || warn "$NAME failed to start"
done

# Wave boundary before the remaining periodic writers.
wait_writer_calm
hdr "12.8 Monitors (loop_watch + graph_refresh -- self-looping, crash-respawn)"
# loop_watch: read-only end-to-end loop watcher; emails on ALERT via /zo/notify.
# graph_refresh: self-healing re-indexer (idle-gated; loads only on a HEAD change).
# Both self-loop via --interval; the bash while-wrapper respawns on crash. We do NOT
# use daemon_wrapper.sh here because it does not forward trailing args (--interval);
# this is the same direct-nohup loop pattern the publisher/governor use (12.6b).
nohup bash -c "while true; do python3 $SENTINEL/loop_watch.py --interval 1800; sleep 30; done" >> $LOGS/loop_watch.log 2>&1 &
sleep 2
LW=$(pgrep -f 'loop_watch.py' 2>/dev/null | head -1)
[[ -n "$LW" ]] && ok "LoopWatch PID $LW" || warn "LoopWatch failed"
nohup bash -c "while true; do python3 $SENTINEL/tools/graph_refresh.py --interval 900; sleep 30; done" >> $LOGS/graph_refresh.log 2>&1 &
sleep 2
GRF=$(pgrep -f 'graph_refresh.py' 2>/dev/null | head -1)
[[ -n "$GRF" ]] && ok "GraphRefresh PID $GRF" || warn "GraphRefresh failed"

hdr "13. World Article Feeder"
nohup python3 $MESH/world_article_feeder.py >> $LOGS/world_article_feeder.log 2>&1 & sleep 2
WAF=$(pgrep -f 'world_article_feeder.py' 2>/dev/null | head -1)
[[ -n "$WAF" ]] && ok "WorldArticleFeeder PID $WAF" || warn "WorldArticleFeeder failed"

hdr "14. World Agent (single instance)"
WA_BEFORE=$(pgrep -f 'run.py --daemon' 2>/dev/null | wc -l)
if [[ "$WA_BEFORE" -gt 0 ]]; then
    pkill -f 'run.py --daemon' 2>/dev/null && warn "killed $WA_BEFORE WorldAgent instance(s)" || true
    sleep 2
fi
cd /home/workspace/world_agent
nohup python run.py --daemon >> $LOGS/world_agent.log 2>&1 &
WA_PID=$!; sleep 3
kill -0 $WA_PID 2>/dev/null && ok "WorldAgent PID $WA_PID" || warn "WorldAgent failed"
cd /home/workspace

hdr "15. Intent Engine"
IE_NOW=$(pgrep -f 'intent_engine_daemon.py' 2>/dev/null | wc -l)
if [[ "$IE_NOW" -eq 0 ]]; then
    nohup python3 /home/workspace/Skills/childofintent-intent-engine/scripts/intent_engine_daemon.py >> $LOGS/intent_engine.log 2>&1 &
    sleep 2; ok "Intent engine started"
else
    ok "Intent engine already running ($IE_NOW instance)"
fi

hdr "16. Ollama smoke test"
python3 - << 'PYEOF'
import urllib.request, json
try:
    p = json.dumps({"task_type":"classify","prompt":"Say OK","agent_id":"t1.test","max_tokens":5}).encode()
    with urllib.request.urlopen(
        urllib.request.Request("http://127.0.0.1:8773/complete",data=p,
            headers={"Content-Type":"application/json"}), timeout=20) as r:
        d = json.loads(r.read())
    tier = d.get('tier_used','?')
    print(f"  tier={tier} -- {'OLLAMA OK' if tier=='ollama' else 'WARNING: not ollama'}")
except Exception as e: print(f"  test failed: {e}")
PYEOF

hdr "17. Watchdog daemon"
WD_RUNNING=$(pgrep -af "python.*watchdog_daemon.py" 2>/dev/null | wc -l | tr -d ' ')
if [[ "$WD_RUNNING" -eq 0 ]]; then
    nohup bash $MESH/daemon_wrapper.sh watchdog_daemon $MESH/watchdog_daemon.py >> $LOGS/watchdog_daemon.log 2>&1 &
    sleep 2
    pgrep -f "watchdog_daemon.py" >/dev/null && ok "watchdog_daemon launched" || warn "watchdog_daemon launch failed"
else
    ok "watchdog_daemon already running ($WD_RUNNING instance)"
fi

hdr "18. ZO-SENTINEL Schema Bootstrap"
# 120s window (bumped 2026-05-28 from 60s): rides out one full wrapper backoff
# cycle (max 60s) plus write_service startup latency. Matches the new
# wait_for_ws default in full_schema_bootstrap.py.
echo "  Waiting up to 120s for write_service..."
WS_READY=0
for i in $(seq 1 40); do
    CODE=$(curl -m5 -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8772/health 2>/dev/null || echo 000)
    if [[ "$CODE" == "200" ]]; then WS_READY=1; break; fi
    sleep 3
done
if [[ "$WS_READY" == "1" ]]; then
    python3 $SENTINEL/full_schema_bootstrap.py 2>&1 | tee $LOGS/bootstrap.log | tail -3
    ok "Sentinel schema bootstrapped"
else
    warn "write_service not ready after 120s -- running bootstrap anyway; it has its own 120s gate"
    python3 $SENTINEL/full_schema_bootstrap.py 2>&1 | tee $LOGS/bootstrap.log | tail -3
fi

hdr "SUMMARY"
echo "  :8772 WriteService:      $(curl -m5 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8772/health 2>/dev/null)"
echo "  :8773 InferenceRouter:   $(curl -m5 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8773/health 2>/dev/null)"
echo "  :8780 ApprovalWorkflow:  $(curl -m5 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8780/health 2>/dev/null)"
echo "  :8781 RegistryAPI:       $(curl -m5 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8781/health 2>/dev/null)"
echo "  :8790 SentinelUI:        $(curl -m5 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8790/health 2>/dev/null)"
echo "  :8795 BuildWatcher:      $(curl -m5 -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8795/health 2>/dev/null)"
echo "  tailscaled procs:        $(pgrep -af tailscaled 2>/dev/null | wc -l | tr -d ' ')"
TP_RUNNING=0
for sc in "${TRUST_PIPELINE[@]}"; do pgrep -f "$sc" >/dev/null 2>&1 && TP_RUNNING=$((TP_RUNNING + 1)); done
MD_RUNNING=0
for sc in "${MESH_DAEMONS[@]}"; do pgrep -f "$sc" >/dev/null 2>&1 && MD_RUNNING=$((MD_RUNNING + 1)); done
echo "  Trust pipeline:          ${TP_RUNNING}/${#TRUST_PIPELINE[@]} daemons up"
echo "  Mesh daemons:            ${MD_RUNNING}/${#MESH_DAEMONS[@]} daemons up"
echo "  ZO_API_KEY:              $([[ -n "$ZO_API_KEY" ]] && echo SET || echo NOT_SET)"
echo "  MINIMAX_API_KEY:         $([[ -n "$MINIMAX_API_KEY" ]] && echo SET || echo NOT_SET)"

# === Boot version marker (telemetry: launcher version becomes one query) ===
# Enumerate the live launcher:  SELECT meta FROM service_health WHERE service='go_sh';
#   -> "go.sh v2.9.2 booted <iso8601>"  -- no file-read or behavioral fingerprint.
# Best-effort; never fails the boot. service_health PK is `service` so this upserts.
_gm=$(mktemp); _gm_ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cat > "$_gm" <<JSON
{"sql": "INSERT INTO service_health (service, status, last_heartbeat, meta) VALUES ('go_sh', 'running', now(), 'go.sh v$GO_SH_VERSION booted $_gm_ts') ON CONFLICT (service) DO UPDATE SET status=excluded.status, last_heartbeat=excluded.last_heartbeat, meta=excluded.meta", "agent_id": "go_sh_boot", "wait": true}
JSON
if curl -m5 -s -X POST http://127.0.0.1:8772/execute -H 'Content-Type: application/json' -d @"$_gm" >/dev/null 2>&1; then
  ok "boot marker: go_sh v$GO_SH_VERSION recorded to service_health"
else
  warn "boot marker write failed (non-fatal)"
fi
rm -f "$_gm"

# === SUPERVISORD_KEEPALIVE_TAIL_v1 ===
if [[ "$SUPERVISORD_KEEPALIVE" == "1" ]]; then
  echo "go.sh: supervisord-keepalive set; exec'ing sleep infinity"
  exec sleep infinity
fi
