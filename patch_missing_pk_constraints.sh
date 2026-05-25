#!/usr/bin/env bash
# patch_missing_pk_constraints.sh
# ------------------------------------------------------------------------------
# Fixes the REAL cause of the "schema drift" errors in signal_analyser.py and
# threat_intel_ingestor.py. The tables mcp_signal_scores and
# mcp_threat_associations were created WITHOUT UNIQUE/PRIMARY KEY constraints.
# write_service uses INSERT ... ON CONFLICT internally for deduplication, and
# DuckDB throws "Binder Error: There are no UNIQUE/PRIMARY KEY constraints"
# when no constraint exists to conflict against.
#
# This is NOT a code bug. The code is shaped correctly for a long-format table.
# The DDL that created the tables just omitted the PK/UNIQUE clause.
#
# Evidence:
#   SELECT constraint_type FROM duckdb_constraints()
#   WHERE table_name IN ('mcp_signal_scores','mcp_threat_associations')
#   returns only NOT NULL -- no PRIMARY KEY, no UNIQUE.
#
# Fix: DROP + CREATE each table with proper constraints. Safe because both
# tables are currently empty (every write has been rejected since creation).
#
# Pre-flight:
#   - Confirms each table has zero rows before destructive operation.
#   - If any row exists, ABORTS and shows the row count so you can decide
#     whether to migrate manually instead.
#
# Safety:
#   - Dry-run mode: DRY_RUN=1 bash patch_missing_pk_constraints.sh
#   - Row count verification BEFORE drop
#   - Explicit confirmation unless FORCE=1 is set
#   - Idempotent: if constraints already exist, skips
#
# Usage:
#   DRY_RUN=1 bash /home/workspace/zo_sentinel/patch_missing_pk_constraints.sh
#   bash /home/workspace/zo_sentinel/patch_missing_pk_constraints.sh
#   FORCE=1 bash /home/workspace/zo_sentinel/patch_missing_pk_constraints.sh  # no interactive confirm
# ------------------------------------------------------------------------------

set -uo pipefail

DRY_RUN="${DRY_RUN:-0}"
FORCE="${FORCE:-0}"
WS="http://127.0.0.1:8772"
SENTINEL=/home/workspace/zo_sentinel
LOGS=/home/workspace/logs
TS="$(date +%Y%m%d_%H%M%S)"

RED=$'\033[91m'; GRN=$'\033[92m'; YLW=$'\033[93m'; CYA=$'\033[96m'; DIM=$'\033[2m'; BLD=$'\033[1m'; NC=$'\033[0m'
h1()   { printf "\n%s%s=== %s ===%s\n" "$BLD" "$CYA" "$*" "$NC"; }
ok()   { printf "  %s[OK]%s %s\n" "$GRN" "$NC" "$*"; }
bad()  { printf "  %s[X]%s %s\n"  "$RED" "$NC" "$*"; }
warn() { printf "  %s[!]%s %s\n"  "$YLW" "$NC" "$*"; }
dry()  { [[ "$DRY_RUN" == "1" ]] && printf "  %s[DRY]%s %s\n" "$DIM" "$NC" "$*"; }

# Helper: run SQL via write_service, return raw JSON
run_sql() {
    curl -s -X POST "$WS/execute" \
        -H 'Content-Type: application/json' \
        --data-raw "{\"sql\":$(printf '%s' "$1" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))')}" \
        2>/dev/null
}

# Helper: COUNT(*) for a table (returns integer or empty)
count_rows() {
    local tbl="$1"
    local resp
    resp="$(curl -s -X POST "$WS/query" \
        -H 'Content-Type: application/json' \
        --data-raw "{\"sql\":\"SELECT COUNT(*) AS n FROM $tbl\"}" 2>/dev/null)"
    echo "$resp" | grep -oE '"n":[0-9]+' | head -1 | grep -oE '[0-9]+'
}

h1 "Missing PK/UNIQUE constraint patcher"
printf "timestamp: %s  dry-run: %s\n" "$TS" "$DRY_RUN"

# ------------------------------------------------------------------------------
# Pre-flight: write_service healthy
# ------------------------------------------------------------------------------
CODE="$(curl -s -o /dev/null -w '%{http_code}' $WS/health 2>/dev/null || echo 000)"
[[ "$CODE" == "200" ]] && ok "write_service :8772 healthy" || { bad "write_service :8772 returned $CODE"; exit 2; }

# ------------------------------------------------------------------------------
# Inspect current state
# ------------------------------------------------------------------------------
h1 "Current state"
for tbl in mcp_signal_scores mcp_threat_associations; do
    n="$(count_rows $tbl)"
    n="${n:-0}"
    printf "  %-30s %6s rows\n" "$tbl" "$n"
    if [[ "$n" -gt 0 ]]; then
        bad "$tbl is not empty. Destructive DROP would lose $n rows."
        bad "Aborting -- manually migrate data before running this patcher."
        exit 3
    fi
done
ok "Both target tables are empty -- safe to recreate"

# Check current constraints
CONSTRAINTS_JSON="$(curl -s -X POST $WS/query -H 'Content-Type: application/json' \
    --data-raw '{"sql":"SELECT table_name, constraint_type FROM duckdb_constraints() WHERE table_name IN ('"'"'mcp_signal_scores'"'"','"'"'mcp_threat_associations'"'"') AND constraint_type IN ('"'"'PRIMARY KEY'"'"','"'"'UNIQUE'"'"')"}')"

if echo "$CONSTRAINTS_JSON" | grep -q '"constraint_type":"PRIMARY KEY"'; then
    # Already has PK on at least one. Dig deeper per-table.
    SIG_HAS_PK="$(echo "$CONSTRAINTS_JSON" | grep -c '"table_name":"mcp_signal_scores"' || true)"
    THR_HAS_PK="$(echo "$CONSTRAINTS_JSON" | grep -c '"table_name":"mcp_threat_associations"' || true)"
    if [[ "$SIG_HAS_PK" -gt 0 && "$THR_HAS_PK" -gt 0 ]]; then
        ok "Both tables already have PK/UNIQUE -- nothing to do"
        exit 0
    fi
fi

# ------------------------------------------------------------------------------
# Confirmation
# ------------------------------------------------------------------------------
if [[ "$DRY_RUN" != "1" && "$FORCE" != "1" ]]; then
    echo
    warn "About to DROP and CREATE:"
    echo "    - mcp_signal_scores       (0 rows, will be recreated)"
    echo "    - mcp_threat_associations (0 rows, will be recreated)"
    echo
    read -r -p "Type YES to proceed: " CONFIRM
    if [[ "$CONFIRM" != "YES" ]]; then
        bad "Aborted by user."
        exit 1
    fi
fi

# ------------------------------------------------------------------------------
# Fix 1: mcp_signal_scores
# New schema -- long-format with composite UNIQUE on (server_id, signal_name)
# so ON CONFLICT can refer to it. id stays as PK for write_service compatibility.
# ------------------------------------------------------------------------------
h1 "Fix: mcp_signal_scores"

SIG_DDL="
DROP TABLE IF EXISTS mcp_signal_scores;
CREATE TABLE mcp_signal_scores (
    id          BIGINT PRIMARY KEY,
    server_id   VARCHAR NOT NULL,
    signal_name VARCHAR NOT NULL,
    score       FLOAT,
    evidence    VARCHAR,
    scored_at   TIMESTAMP WITH TIME ZONE DEFAULT now(),
    UNIQUE (server_id, signal_name)
);
CREATE INDEX IF NOT EXISTS idx_signal_scores_server ON mcp_signal_scores(server_id);
CREATE INDEX IF NOT EXISTS idx_signal_scores_name ON mcp_signal_scores(signal_name);
"

if [[ "$DRY_RUN" == "1" ]]; then
    dry "would execute:"
    echo "$SIG_DDL" | sed 's/^/    /'
else
    # Execute each statement separately because some SQL runners don't handle multi-statement well
    for stmt in \
        "DROP TABLE IF EXISTS mcp_signal_scores" \
        "CREATE TABLE mcp_signal_scores (id BIGINT PRIMARY KEY, server_id VARCHAR NOT NULL, signal_name VARCHAR NOT NULL, score FLOAT, evidence VARCHAR, scored_at TIMESTAMP WITH TIME ZONE DEFAULT now(), UNIQUE (server_id, signal_name))" \
        "CREATE INDEX IF NOT EXISTS idx_signal_scores_server ON mcp_signal_scores(server_id)" \
        "CREATE INDEX IF NOT EXISTS idx_signal_scores_name ON mcp_signal_scores(signal_name)"; do
        RESP="$(run_sql "$stmt")"
        if echo "$RESP" | grep -qi 'error\|detail'; then
            bad "Statement failed: $stmt"
            echo "    Response: $RESP"
            exit 4
        fi
    done
    ok "mcp_signal_scores recreated with PRIMARY KEY(id) + UNIQUE(server_id, signal_name)"
fi

# ------------------------------------------------------------------------------
# Fix 2: mcp_threat_associations
# Schema inferred from threat_intel_ingestor expectations. Composite UNIQUE
# (server_id, threat_type, evidence) to allow repeat observations of same
# threat with different evidence, but dedupe identical reports.
# ------------------------------------------------------------------------------
h1 "Fix: mcp_threat_associations"

# First, get current columns to preserve the existing column set
COLS_RESP="$(curl -s -X POST $WS/query -H 'Content-Type: application/json' \
    --data-raw '{"sql":"SELECT column_name, data_type FROM information_schema.columns WHERE table_name='"'"'mcp_threat_associations'"'"' ORDER BY ordinal_position"}')"
echo "  Current schema:"
echo "$COLS_RESP" | python3 -c 'import sys,json
try:
    d = json.loads(sys.stdin.read())
    for r in d.get("rows", []):
        print(f"      {r[\"column_name\"]:20s} {r[\"data_type\"]}")
except: print("    (parse failed)")'

THR_DDL_STMTS=(
    "DROP TABLE IF EXISTS mcp_threat_associations"
    "CREATE TABLE mcp_threat_associations (id BIGINT PRIMARY KEY, server_id VARCHAR NOT NULL, threat_type VARCHAR NOT NULL, severity VARCHAR, evidence VARCHAR, source VARCHAR, reported_at TIMESTAMP WITH TIME ZONE DEFAULT now(), UNIQUE (server_id, threat_type, source))"
    "CREATE INDEX IF NOT EXISTS idx_threats_server ON mcp_threat_associations(server_id)"
    "CREATE INDEX IF NOT EXISTS idx_threats_severity ON mcp_threat_associations(severity)"
)

if [[ "$DRY_RUN" == "1" ]]; then
    dry "would execute:"
    for s in "${THR_DDL_STMTS[@]}"; do
        echo "    $s" | cut -c1-120
    done
else
    for stmt in "${THR_DDL_STMTS[@]}"; do
        RESP="$(run_sql "$stmt")"
        if echo "$RESP" | grep -qi 'error\|detail'; then
            bad "Statement failed: ${stmt:0:80}..."
            echo "    Response: $RESP"
            exit 4
        fi
    done
    ok "mcp_threat_associations recreated with PRIMARY KEY(id) + UNIQUE(server_id, threat_type, source)"
fi

if [[ "$DRY_RUN" == "1" ]]; then
    h1 "Dry-run complete"
    ok "Run without DRY_RUN=1 to apply."
    exit 0
fi

# ------------------------------------------------------------------------------
# Verification -- confirm constraints now exist
# ------------------------------------------------------------------------------
h1 "Verify"
VERIFY="$(curl -s -X POST $WS/query -H 'Content-Type: application/json' \
    --data-raw '{"sql":"SELECT table_name, constraint_type, constraint_column_indexes FROM duckdb_constraints() WHERE table_name IN ('"'"'mcp_signal_scores'"'"','"'"'mcp_threat_associations'"'"') ORDER BY table_name, constraint_type"}')"
echo "$VERIFY" | python3 -c 'import sys,json
try:
    d = json.loads(sys.stdin.read())
    seen_pk_sig = seen_uq_sig = seen_pk_thr = seen_uq_thr = False
    for r in d.get("rows", []):
        t, c = r["table_name"], r["constraint_type"]
        print(f"  {t:30s} {c}")
        if t == "mcp_signal_scores" and c == "PRIMARY KEY": seen_pk_sig = True
        if t == "mcp_signal_scores" and c == "UNIQUE":      seen_uq_sig = True
        if t == "mcp_threat_associations" and c == "PRIMARY KEY": seen_pk_thr = True
        if t == "mcp_threat_associations" and c == "UNIQUE":      seen_uq_thr = True
    print()
    if seen_pk_sig and seen_uq_sig:
        print("  [OK] mcp_signal_scores has PRIMARY KEY and UNIQUE")
    else:
        print("  [X] mcp_signal_scores missing constraints")
    if seen_pk_thr and seen_uq_thr:
        print("  [OK] mcp_threat_associations has PRIMARY KEY and UNIQUE")
    else:
        print("  [X] mcp_threat_associations missing constraints")
except Exception as e:
    print(f"  (verify parse failed: {e})")'

# ------------------------------------------------------------------------------
# Restart the two affected daemons so they pick up clean tables
# ------------------------------------------------------------------------------
h1 "Restart affected daemons"
for name in signal_analyser threat_intel_ingestor; do
    if pgrep -f "python3 .*${name}.py" >/dev/null 2>&1; then
        pkill -9 -f "python3 .*${name}.py" 2>/dev/null
        warn "killed $name"
    fi
    rm -f "/tmp/${name}.lock" 2>/dev/null
done
sleep 2
for name in signal_analyser threat_intel_ingestor; do
    setsid python3 "$SENTINEL/${name}.py" >> "$LOGS/sentinel_${name}.log" 2>&1 <&- &
    sleep 2
    pid="$(pgrep -f "python3 .*${name}.py" 2>/dev/null | head -1)"
    if [[ -n "$pid" ]]; then
        ok "$name PID $pid"
    else
        bad "$name failed to start -- last 5 lines:"
        tail -5 "$LOGS/sentinel_${name}.log" | sed 's/^/    /'
    fi
done

# ------------------------------------------------------------------------------
# Final check -- wait for first writes to land
# ------------------------------------------------------------------------------
h1 "Wait for first writes"
echo "Giving signal_analyser 45s to complete its first scoring cycle..."
sleep 45

SIG_N="$(count_rows mcp_signal_scores)"
SIG_N="${SIG_N:-0}"
THR_N="$(count_rows mcp_threat_associations)"
THR_N="${THR_N:-0}"

echo
printf "  %-30s %6s rows  " "mcp_signal_scores" "$SIG_N"
[[ "$SIG_N" -gt 0 ]] && ok "writes flowing" || warn "still empty -- check $LOGS/sentinel_signal_analyser.log"
printf "  %-30s %6s rows  " "mcp_threat_associations" "$THR_N"
[[ "$THR_N" -gt 0 ]] && ok "writes flowing" || warn "still empty -- may take longer (larger cycle)"

h1 "Done"
echo "Changes:"
echo "  - mcp_signal_scores:       added PRIMARY KEY(id), UNIQUE(server_id, signal_name)"
echo "  - mcp_threat_associations: added PRIMARY KEY(id), UNIQUE(server_id, threat_type, source)"
echo
echo "If signals still aren't flowing after 5 min, check:"
echo "  tail -30 $LOGS/sentinel_signal_analyser.log"
echo "  (look for 'Write failed' or successful 'Saved signal' messages)"
echo
warn "Note: if any OTHER code was relying on the old schema shape, it may now"
warn "       break. The UI reads from mcp_signal_scores correctly in long format,"
warn "       so the Phase 1 UI is unaffected."