#!/usr/bin/env bash
# create_failure_matrix_view.sh -- Phase 4b: create the failure_matrix view over
# build_provenance via write_service :8772. Idempotent (CREATE OR REPLACE VIEW);
# touches no data. Mirrors full_schema_bootstrap.py's view DDL exactly.
#
# Run ONE command:  bash /home/workspace/zo_sentinel/create_failure_matrix_view.sh
set -euo pipefail
BUS=http://127.0.0.1:8772
LOG=/home/workspace/logs/create_failure_matrix_view.log

# Single-line DDL (no quotes inside, so it embeds directly in the JSON body --
# no jq dependency, consistent with the other host scripts).
SQL='CREATE OR REPLACE VIEW failure_matrix AS SELECT directive_type, complexity, model, COUNT(*) AS attempts, SUM(CASE WHEN success THEN 1 ELSE 0 END) AS successes, ROUND(100.0 * AVG(CASE WHEN success THEN 1 ELSE 0 END), 1) AS success_pct, ROUND(AVG(rescue_count), 2) AS avg_rescues, MAX(built_at) AS last_seen, arg_max(error, built_at) FILTER (WHERE NOT success) AS last_error FROM build_provenance GROUP BY directive_type, complexity, model'

{
echo "=== create_failure_matrix_view start $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

echo "[*] creating view..."
curl -s --max-time 30 -X POST "$BUS/execute" -H 'Content-Type: application/json' \
  -d "{\"sql\": \"$SQL\", \"agent_id\": \"host_phase4_view\", \"wait\": true}"; echo

echo "[*] verify -- the view is queryable (empty until builds record provenance):"
curl -s --max-time 20 -X POST "$BUS/query" -H 'Content-Type: application/json' \
  -d '{"sql":"SELECT COUNT(*) AS matrix_rows FROM failure_matrix"}'; echo
curl -s --max-time 20 -X POST "$BUS/query" -H 'Content-Type: application/json' \
  -d '{"sql":"SELECT COUNT(*) AS provenance_rows FROM build_provenance"}'; echo

echo "=== create_failure_matrix_view done $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
} 2>&1 | tee -a "$LOG"
