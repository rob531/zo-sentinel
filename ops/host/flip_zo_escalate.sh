#!/usr/bin/env bash
# flip_zo_escalate.sh -- turn the Phase-5 escalation edge ON, durably, and bring
# goose_runner onto the latest code (incl. the routed-model provenance fix #113).
#
# Durability model:
#   - .zo_env: go.sh sources it (set -a) on every `zm go`, so a full boot keeps
#     ZO_ESCALATE=1. This is the canonical home for the flag.
#   - immediate relaunch below makes it active NOW without waiting for a boot.
#   The mesh watchdog (v3.6+) respawns goose_runner with `env ZO_ESCALATE=1`, so
#   a crash no longer drops escalation either.
#
# To turn OFF later: remove the ZO_ESCALATE line from .zo_env, revert the
# watchdog GooseRunner line to plain python3, and relaunch.
#
# Run ONE command:  bash /home/workspace/zo_sentinel/flip_zo_escalate.sh
set -uo pipefail
LOGS=/home/workspace/logs; MESH=/home/workspace/zo_mesh; SENTINEL=/home/workspace/zo_sentinel
ENVF="$MESH/.zo_env"
LOG="$LOGS/flip_zo_escalate.log"
{
echo "=== flip_zo_escalate $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

# 0. refresh code so the relaunch runs the routed-model provenance fix (#113)
cd "$SENTINEL" || { echo "FATAL: cannot cd $SENTINEL"; exit 1; }
git fetch origin main -q && git reset --hard origin/main -q
echo "  zo_sentinel HEAD: $(git rev-parse --short HEAD)"

# 1. durable flag in the shared env file (idempotent)
if grep -q '^export ZO_ESCALATE=' "$ENVF" 2>/dev/null; then
  echo "  .zo_env already sets ZO_ESCALATE (left as-is)"
else
  echo 'export ZO_ESCALATE=1' >> "$ENVF"
  echo "  appended 'export ZO_ESCALATE=1' to .zo_env"
fi

# 2. relaunch goose_runner NOW with the flag active
echo "[*] restarting goose_runner with ZO_ESCALATE=1..."
pkill -f '[g]oose_runner.py' 2>/dev/null && echo "  stopped old goose_runner" || echo "  (none running)"
sleep 2
pgrep -f '[g]oose_runner.py' >/dev/null || ZO_ESCALATE=1 nohup python3 "$SENTINEL/goose_runner.py" >> "$LOGS/goose_runner.log" 2>&1 &
sleep 2
PID=$(pgrep -f '[g]oose_runner.py' | head -1)
echo "  goose_runner pid: ${PID:-NOT RUNNING}"

# 3. verify the flag is in the RUNNING process environment
if [[ -n "${PID:-}" ]] && tr '\0' '\n' < "/proc/$PID/environ" 2>/dev/null | grep -q '^ZO_ESCALATE=1'; then
  echo "  VERIFIED: ZO_ESCALATE=1 in goose_runner pid $PID -- escalation ARMED"
else
  echo "  WARN: could not confirm ZO_ESCALATE in pid ${PID:-?} environ"
fi

# 4. show the matrix so you can watch it fill (model column now = routed rung)
echo "[*] failure_matrix snapshot:"
curl -s --max-time 20 -X POST http://127.0.0.1:8772/query -H 'Content-Type: application/json' \
  -d '{"sql":"SELECT directive_type, complexity, model, attempts, success_pct, avg_rescues FROM failure_matrix ORDER BY attempts DESC LIMIT 12"}'; echo

echo "=== done -- escalation ON; watch build_provenance/failure_matrix grow ==="
} 2>&1 | tee "$LOG"
