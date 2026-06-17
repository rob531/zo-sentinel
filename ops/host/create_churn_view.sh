#!/usr/bin/env bash
# create_churn_view.sh -- create the convergence leading-indicator views
# (build_churn_daily + build_churn_trend) over build_provenance via
# write_service :8772. Idempotent (CREATE OR REPLACE VIEW); touches no data.
# Mirrors schema/builder.duckdb.sql + full_schema_bootstrap.py exactly.
#
# These views answer "is the builder CONVERGING or PLATEAUING?" from the
# build_provenance corpus: build_churn_trend.regime is the one row to watch.
#
# Run ONE command:  bash /home/workspace/zo_sentinel/create_churn_view.sh
#
# The view DDL contains single quotes (regexp literals), so we write the JSON
# payloads via quoted heredocs and POST with `curl -d @file` -- no quote
# gymnastics, no jq dependency.
set -euo pipefail
BUS=http://127.0.0.1:8772
LOG=/home/workspace/logs/create_churn_view.log
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

cat > "$TMP/daily.json" <<'JSON'
{"agent_id":"host_churn_view","wait":true,"sql":"CREATE OR REPLACE VIEW build_churn_daily AS WITH produced AS (SELECT build_id, built_at::DATE AS day, success, rescue_count, output_path, regexp_matches(output_path, '_v[0-9]') AS is_versioned, regexp_matches(output_path, '(wiring|integration|completion|complete|fix|patch|diagnose|verify|_test)') AS is_glue FROM build_provenance WHERE COALESCE(output_path, '') <> ''), first_day AS (SELECT output_path, MIN(day) AS first_day FROM produced GROUP BY output_path), churned AS (SELECT p.*, (fd.first_day < p.day) AS is_path_rework, ((fd.first_day < p.day) OR p.is_versioned OR p.is_glue) AS is_churn FROM produced p JOIN first_day fd ON fd.output_path = p.output_path) SELECT day, COUNT(*) AS produced_files, SUM(is_churn::INT) AS churn_files, SUM((NOT is_churn)::INT) AS net_new_files, ROUND(100.0 * AVG(is_churn::INT), 1) AS churn_pct, ROUND(100.0 * AVG(success::INT), 1) AS success_pct, ROUND(AVG(rescue_count), 2) AS avg_rescues, SUM(is_path_rework::INT) AS path_rework, SUM(is_versioned::INT) AS versioned, SUM(is_glue::INT) AS glue FROM churned GROUP BY day ORDER BY day DESC"}
JSON

cat > "$TMP/trend.json" <<'JSON'
{"agent_id":"host_churn_view","wait":true,"sql":"CREATE OR REPLACE VIEW build_churn_trend AS SELECT cur.churn_pct_7d, prev.churn_pct_7d AS churn_pct_prev_7d, ROUND(cur.churn_pct_7d - prev.churn_pct_7d, 1) AS delta_pts, CASE WHEN cur.churn_pct_7d IS NULL THEN 'NO-DATA' WHEN prev.churn_pct_7d IS NULL THEN 'BASELINE' WHEN cur.churn_pct_7d - prev.churn_pct_7d <= -3 THEN 'CONVERGING' WHEN cur.churn_pct_7d - prev.churn_pct_7d >= 3 THEN 'PLATEAU-RISK' ELSE 'FLAT' END AS regime FROM (SELECT ROUND(100.0 * SUM(churn_files) / NULLIF(SUM(produced_files), 0), 1) AS churn_pct_7d FROM build_churn_daily WHERE day > CURRENT_DATE - INTERVAL 7 DAY) cur, (SELECT ROUND(100.0 * SUM(churn_files) / NULLIF(SUM(produced_files), 0), 1) AS churn_pct_7d FROM build_churn_daily WHERE day > CURRENT_DATE - INTERVAL 14 DAY AND day <= CURRENT_DATE - INTERVAL 7 DAY) prev"}
JSON

{
echo "=== create_churn_view start $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

echo "[*] creating build_churn_daily ..."
curl -s --max-time 30 -X POST "$BUS/execute" -H 'Content-Type: application/json' -d @"$TMP/daily.json"; echo
echo "[*] creating build_churn_trend ..."
curl -s --max-time 30 -X POST "$BUS/execute" -H 'Content-Type: application/json' -d @"$TMP/trend.json"; echo

echo "[*] verify -- daily rows (empty until builds record provenance with output_path):"
curl -s --max-time 20 -X POST "$BUS/query" -H 'Content-Type: application/json' \
  -d '{"sql":"SELECT * FROM build_churn_daily LIMIT 14"}'; echo
echo "[*] verify -- the regime verdict:"
curl -s --max-time 20 -X POST "$BUS/query" -H 'Content-Type: application/json' \
  -d '{"sql":"SELECT * FROM build_churn_trend"}'; echo

echo "=== create_churn_view done $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
} 2>&1 | tee -a "$LOG"
