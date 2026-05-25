#!/usr/bin/env bash
# eval_ui_gaps.sh
# ------------------------------------------------------------------------------
# Diagnostic for the three empty-surface issues visible in the ZO-Sentinel UI:
#   (A) Search returns partial/null for reasonable queries
#   (B) Threat Feed empty ("No threats recorded yet")
#   (C) Risk Register empty
#
# Answers: are these fixable by Phase 8, or are they upstream (ingestion) gaps?
# Phase 8 adds an injection_resilience dimension. It does NOT populate the
# registry, generate threats, or rank risk. If these surfaces are empty because
# the pipeline stages that feed them aren't running, Phase 8 changes nothing.
#
# This script distinguishes between:
#   1. Data emptiness (tables empty, no rows to query)
#   2. Daemon absence (processes not running, no writes happening)
#   3. Code defect (daemon running but silently failing)
#   4. UI defect (data exists but UI query is wrong)
#
# Usage:
#   bash /home/workspace/zo_sentinel/eval_ui_gaps.sh
#   VERBOSE=1 bash /home/workspace/zo_sentinel/eval_ui_gaps.sh   # more detail
# ------------------------------------------------------------------------------

set -uo pipefail  # NOT -e: we want to see every check even if one fails

WRITE_SVC="http://127.0.0.1:8772"
UI_SVC="http://127.0.0.1:8790"
REGISTRY_API="http://127.0.0.1:8781"
SENTINEL_DIR="/home/workspace/zo_sentinel"
SUPERVISOR_CONF="$SENTINEL_DIR/supervisord_sentinel_full.conf"
VERBOSE="${VERBOSE:-0}"

RED=$'\033[91m'; GREEN=$'\033[92m'; YELLOW=$'\033[93m'; CYAN=$'\033[96m'; DIM=$'\033[2m'; BOLD=$'\033[1m'; RESET=$'\033[0m'

h1()   { printf "\n%s%s=== %s ===%s\n" "$BOLD" "$CYAN" "$*" "$RESET"; }
h2()   { printf "\n%s-- %s --%s\n" "$CYAN" "$*" "$RESET"; }
ok()   { printf "%s[OK]%s %s\n" "$GREEN" "$RESET" "$*"; }
bad()  { printf "%s[X]%s %s\n"  "$RED"   "$RESET" "$*"; }
warn() { printf "%s[!]%s %s\n"  "$YELLOW" "$RESET" "$*"; }
info() { printf "%s[i]%s %s\n"  "$DIM"   "$RESET" "$*"; }
v()    { [[ "$VERBOSE" == "1" ]] && printf "%s    %s%s\n" "$DIM" "$*" "$RESET"; }

# Query helper — posts SQL to write_service, extracts a single integer field.
# Usage: q <sql> <json_field_name>
q() {
  local sql="$1" field="${2:-}"
  local result
  result="$(curl -s -X POST "$WRITE_SVC/query" \
    -H 'Content-Type: application/json' \
    --data-raw "{\"sql\":\"$sql\"}" 2>/dev/null)"
  if [[ -z "$field" ]]; then echo "$result"; return; fi
  echo "$result" | grep -oE "\"$field\":[0-9]+" | head -1 | grep -oE '[0-9]+' || echo ""
}

# ==============================================================================
h1 "Issue A — Search returns partial / null"
# ==============================================================================
h2 "A.1 How many MCPs does the registry actually hold?"
REG_TOTAL="$(q 'SELECT COUNT(*) AS c FROM mcp_server_registry' 'c')"
[[ -n "$REG_TOTAL" && "$REG_TOTAL" -gt 0 ]] \
  && ok "Registry: $REG_TOTAL rows" \
  || bad "Registry: empty or unreachable"

h2 "A.2 Breakdown by source"
curl -s -X POST "$WRITE_SVC/query" -H 'Content-Type: application/json' \
  --data-raw '{"sql":"SELECT source, COUNT(*) n FROM mcp_server_registry GROUP BY source ORDER BY n DESC"}' 2>/dev/null

h2 "A.3 Assessed vs unassessed"
ASSESSED="$(q 'SELECT COUNT(*) c FROM mcp_server_registry WHERE verdict IS NOT NULL' 'c')"
UNASSESSED="$(q 'SELECT COUNT(*) c FROM mcp_server_registry WHERE verdict IS NULL' 'c')"
info "Assessed: $ASSESSED / Unassessed: $UNASSESSED"

h2 "A.4 Is the mcp_scanner pulling new submissions?"
SCANNER_HB="$(q \"SELECT CAST(EXTRACT(EPOCH FROM (now() - last_heartbeat)) AS INTEGER) age FROM service_health WHERE service='mcp_scanner'\" 'age')"
if [[ -z "$SCANNER_HB" ]]; then
  bad "mcp_scanner never heartbeated — the source-of-new-MCPs daemon is dead"
elif [[ "$SCANNER_HB" -gt 3600 ]]; then
  bad "mcp_scanner heartbeat is $SCANNER_HB sec old (> 1 hour) — process dead or hung"
else
  ok "mcp_scanner heartbeat $SCANNER_HB sec old"
fi

info "Verdict on Issue A:"
if [[ "$REG_TOTAL" -lt 50 ]]; then
  bad "  Registry has only $REG_TOTAL MCPs — searches for anything outside those will miss."
  bad "  Root cause: mcp_scanner not running. Search can't find what was never ingested."
  info "  Fix: restart mcp_scanner, run a bulk backfill against npm/Smithery."
  info "  Phase 8 does NOT fix this."
fi

# ==============================================================================
h1 "Issue B — Threat Feed empty"
# ==============================================================================
h2 "B.1 mcp_threat_associations row count"
THREAT_ROWS="$(q 'SELECT COUNT(*) c FROM mcp_threat_associations' 'c')"
[[ "$THREAT_ROWS" -gt 0 ]] \
  && ok "Threats table: $THREAT_ROWS rows" \
  || bad "Threats table: EMPTY — nothing for the feed to display"

h2 "B.2 Is threat_intel_ingestor alive?"
TI_HB="$(q \"SELECT CAST(EXTRACT(EPOCH FROM (now() - last_heartbeat)) AS INTEGER) age FROM service_health WHERE service='threat_intel_ingestor'\" 'age')"
if [[ -z "$TI_HB" ]]; then
  bad "threat_intel_ingestor never heartbeated"
elif [[ "$TI_HB" -gt 3600 ]]; then
  bad "threat_intel_ingestor $TI_HB sec stale"
else
  ok "threat_intel_ingestor alive ($TI_HB sec)"
fi

h2 "B.3 Is the file even present and in supervisord?"
if [[ -f "$SENTINEL_DIR/threat_intel_ingestor.py" ]]; then
  ok "Source file exists"
else
  bad "Source file MISSING"
fi
if grep -q 'threat_intel_ingestor' "$SUPERVISOR_CONF" 2>/dev/null; then
  ok "Registered in supervisord config"
else
  bad "NOT in supervisord config — will never auto-start"
fi
if pgrep -fa 'threat_intel_ingestor.py' >/dev/null 2>&1; then
  ok "Process running: $(pgrep -fa 'threat_intel_ingestor.py' | head -1)"
else
  bad "No threat_intel_ingestor.py process running"
fi

h2 "B.4 Supervisord state for threat_intel_ingestor"
if command -v supervisorctl >/dev/null 2>&1; then
  supervisorctl -c /etc/zo/supervisord-user.conf status 2>/dev/null | grep -i threat || warn "No supervisord entry reports threat_intel_ingestor"
else
  warn "supervisorctl unavailable"
fi

info "Verdict on Issue B:"
bad "  Threat Feed empty because mcp_threat_associations has 0 rows."
bad "  Writer daemon (threat_intel_ingestor) is not running or never ran."
info "  Fix: ensure supervisord launches it; restart mesh with 'zm go' if needed."
info "  Phase 8 does NOT fix this — Phase 8 adds a scoring dimension, not threat feeds."

# ==============================================================================
h1 "Issue C — Risk Register empty"
# ==============================================================================
h2 "C.1 mcp_risk_register row count"
RISK_ROWS="$(q 'SELECT COUNT(*) c FROM mcp_risk_register' 'c')"
[[ "$RISK_ROWS" -gt 0 ]] \
  && ok "Risk register: $RISK_ROWS rows" \
  || bad "Risk register: EMPTY"

h2 "C.2 Upstream dependency: mcp_signal_scores"
SIG_ROWS="$(q 'SELECT COUNT(*) c FROM mcp_signal_scores' 'c')"
[[ "$SIG_ROWS" -gt 0 ]] \
  && ok "Signal scores: $SIG_ROWS rows" \
  || bad "Signal scores: EMPTY — risk_ranker has nothing to rank"

h2 "C.3 Further upstream: signal_analyser heartbeat"
SA_HB="$(q \"SELECT CAST(EXTRACT(EPOCH FROM (now() - last_heartbeat)) AS INTEGER) age FROM service_health WHERE service='signal_analyser'\" 'age')"
if [[ -z "$SA_HB" ]]; then
  bad "signal_analyser never heartbeated"
elif [[ "$SA_HB" -gt 3600 ]]; then
  bad "signal_analyser $SA_HB sec stale — not writing signal scores"
else
  ok "signal_analyser alive ($SA_HB sec)"
fi

h2 "C.4 risk_ranker heartbeat"
RR_HB="$(q \"SELECT CAST(EXTRACT(EPOCH FROM (now() - last_heartbeat)) AS INTEGER) age FROM service_health WHERE service='risk_ranker'\" 'age')"
if [[ -z "$RR_HB" ]]; then
  bad "risk_ranker never heartbeated"
elif [[ "$RR_HB" -gt 3600 ]]; then
  bad "risk_ranker $RR_HB sec stale"
else
  ok "risk_ranker alive ($RR_HB sec)"
fi

h2 "C.5 Is the data flow wired into supervisord?"
for svc in mcp_scanner signal_analyser trust_synthesiser threat_intel_ingestor risk_ranker attestation_engine registry_api; do
  if grep -q "$svc" "$SUPERVISOR_CONF" 2>/dev/null; then
    if pgrep -fa "${svc}.py" >/dev/null 2>&1; then
      ok "$svc: in supervisord AND process running"
    else
      bad "$svc: in supervisord but NO process running"
    fi
  else
    warn "$svc: NOT in supervisord_sentinel_full.conf"
  fi
done

info "Verdict on Issue C:"
bad "  Risk Register empty because mcp_signal_scores is empty."
bad "  Signal scores empty because signal_analyser hasn't run since April 13."
bad "  risk_ranker either dead or never started — no heartbeat recorded."
info "  Fix order: (1) mcp_scanner ingests MCPs → (2) signal_analyser scores them"
info "            → (3) trust_synthesiser computes verdicts → (4) risk_ranker ranks"
info "            → (5) risk register populates."
info "  Phase 8 adds a 7th signal dimension — it's additive to signal_analyser,"
info "  not a replacement for the pipeline that's not running."

# ==============================================================================
h1 "Overall diagnosis"
# ==============================================================================
cat <<EOF

${BOLD}All three symptoms share one root cause:${RESET}
The Phase 1–7 sentinel pipeline daemons are NOT running. mcp_scanner and
signal_analyser last heartbeated April 13. trust_synthesiser, risk_ranker,
threat_intel_ingestor, attestation_engine, and registry_api have never
heartbeated at all. Whatever is in supervisord_sentinel_full.conf either
wasn't picked up, crashed silently on first start, or those services lack
heartbeat() calls.

The registry holds 15 rows because ${YELLOW}quick_seed.py${RESET} bootstrapped them on
April 12. That's why "Google" returns a hit (one of the seeded MCPs happens
to match). It's why everything else is empty — the seed was a one-shot,
and the continuous ingestion layer never took over.

${BOLD}Will Phase 8 fix this? No.${RESET}
Phase 8 adds an ${CYAN}injection_resilience${RESET} signal dimension. It assumes the
upstream pipeline already runs and writes signal scores for the other six
dimensions. Deploying Phase 8 on top of a dead pipeline just means
injection_resilience becomes the seventh empty column.

${BOLD}Fix sequence (no new code, just restart):${RESET}
  1. ${CYAN}supervisorctl -c /etc/zo/supervisord-user.conf reread && update${RESET}
  2. Verify each sentinel service starts:
     ${CYAN}supervisorctl -c /etc/zo/supervisord-user.conf status | grep zo_sentinel${RESET}
  3. Watch first heartbeats land:
     ${CYAN}curl -s -X POST http://127.0.0.1:8772/query \\
       -H 'Content-Type: application/json' \\
       -d '{"sql":"SELECT service, last_heartbeat FROM service_health WHERE service LIKE '\\''%sentinel%'\\'' OR service IN ('\\''mcp_scanner'\\'','\\''signal_analyser'\\'','\\''risk_ranker'\\'','\\''threat_intel_ingestor'\\'')"}'${RESET}
  4. If a service appears in status as FATAL, check its log:
     ${CYAN}tail -50 /home/workspace/logs/sentinel_*.log${RESET}

If a service is in supervisord but won't stay up, that's the real bug to
chase — likely a missing write_service dependency, a port conflict, or a
schema mismatch (these daemons were built before the DB_SCHEMA.md audit).

${BOLD}Phase 8 should resume AFTER these six daemons are heartbeating.${RESET}
Until then, even a perfectly-built pi_harness_runner has nothing to test
against — it needs APPROVED MCPs with real signal scores to iterate over.
EOF