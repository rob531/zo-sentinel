#!/usr/bin/env bash
# deploy_phase45.sh -- bring Phase 4/5 (PR #112) LIVE on the box in one paste-run:
#   1. refresh zo_sentinel to origin/main (new goose_runner + build_routing +
#      builder_mcp + full_schema_bootstrap)
#   2. ensure the failure_matrix view exists (idempotent CREATE OR REPLACE)
#   3. restart goose_runner so it runs the new code (it never self-recovers;
#      watchdog.sh v3.5 is only the ~6-9min backstop, so relaunch explicitly)
#   4. verify HEAD + daemon + view/provenance counts
#
# builder_mcp is spawned per-session by goose over stdio, so it reads the
# refreshed builder_mcp.py automatically on the next build -- no separate restart.
# Escalation stays OFF: ZO_ESCALATE is not set here. Flip it later, separately,
# once build_provenance has accrued rows.
#
# Run ONE command:  bash /home/workspace/zo_sentinel/deploy_phase45.sh
set -uo pipefail
SENT=/home/workspace/zo_sentinel
BUS=http://127.0.0.1:8772
LOG=/home/workspace/logs/deploy_phase45.log
VIEW_SQL='CREATE OR REPLACE VIEW failure_matrix AS SELECT directive_type, complexity, model, COUNT(*) AS attempts, SUM(CASE WHEN success THEN 1 ELSE 0 END) AS successes, ROUND(100.0 * AVG(CASE WHEN success THEN 1 ELSE 0 END), 1) AS success_pct, ROUND(AVG(rescue_count), 2) AS avg_rescues, MAX(built_at) AS last_seen, arg_max(error, built_at) FILTER (WHERE NOT success) AS last_error FROM build_provenance GROUP BY directive_type, complexity, model'
{
echo "=== deploy_phase45 start $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

# 1. refresh code
cd "$SENT" || { echo "FATAL: cannot cd $SENT"; exit 1; }
git fetch origin main -q && git reset --hard origin/main -q
echo "  HEAD: $(git rev-parse --short HEAD)  $(git log -1 --pretty=%s | cut -c1-60)"
python3 -c "import ast; ast.parse(open('goose_runner.py').read()); ast.parse(open('zo_sentinel/build_routing.py').read()); print('  syntax OK: goose_runner + build_routing')"

# 2. ensure the matrix view (idempotent; no data touched)
echo "[*] ensure failure_matrix view..."
curl -s --max-time 30 -X POST "$BUS/execute" -H 'Content-Type: application/json' \
  -d "{\"sql\": \"$VIEW_SQL\", \"agent_id\": \"deploy_phase45\", \"wait\": true}"; echo

# 3. restart goose_runner onto the new code (pkill then pgrep-guarded relaunch)
echo "[*] restarting goose_runner..."
pkill -f '[g]oose_runner.py' 2>/dev/null && echo "  stopped old goose_runner" || echo "  (none running)"
sleep 2
pgrep -f '[g]oose_runner.py' >/dev/null || nohup python3 "$SENT/goose_runner.py" >> /home/workspace/logs/goose_runner.log 2>&1 &
sleep 2
NEWPID=$(pgrep -f '[g]oose_runner.py' | head -1)
echo "  goose_runner pid: ${NEWPID:-NOT RUNNING}"

# 4. verify
echo "[*] verify:"
curl -s --max-time 20 -X POST "$BUS/query" -H 'Content-Type: application/json' \
  -d '{"sql":"SELECT COUNT(*) AS provenance_rows FROM build_provenance"}'; echo
curl -s --max-time 20 -X POST "$BUS/query" -H 'Content-Type: application/json' \
  -d '{"sql":"SELECT COUNT(*) AS matrix_rows FROM failure_matrix"}'; echo

echo "=== deploy_phase45 done $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "NEXT: watch build_provenance climb as directives build; flip ZO_ESCALATE=1 later when ready."
} 2>&1 | tee -a "$LOG"
