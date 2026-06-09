#!/usr/bin/env bash
# verify_escalation_armed.sh -- definitively confirm goose_runner is running with
# ZO_ESCALATE=1; if not, re-arm using the `env` form (the one the watchdog uses,
# which propagates unambiguously). Read-only unless it needs to re-arm.
set -uo pipefail
SENTINEL=/home/workspace/zo_sentinel; LOGS=/home/workspace/logs
P=$(pgrep -f '[g]oose_runner.py' | head -1)
echo "goose_runner pid: ${P:-NONE}"
if [[ -n "${P:-}" ]] && tr '\0' '\n' < "/proc/$P/environ" 2>/dev/null | grep -q '^ZO_ESCALATE=1'; then
  echo "ARMED: pid $P has ZO_ESCALATE=1 -- escalation active"
else
  echo "NOT armed on pid ${P:-?} -- re-arming via 'env' form..."
  pkill -f '[g]oose_runner.py' 2>/dev/null; sleep 2
  nohup env ZO_ESCALATE=1 python3 "$SENTINEL/goose_runner.py" >> "$LOGS/goose_runner.log" 2>&1 &
  sleep 2
  P2=$(pgrep -f '[g]oose_runner.py' | head -1)
  if [[ -n "${P2:-}" ]] && tr '\0' '\n' < "/proc/$P2/environ" 2>/dev/null | grep -q '^ZO_ESCALATE=1'; then
    echo "ARMED: pid $P2 has ZO_ESCALATE=1 (re-armed with env form)"
  else
    echo "STILL not confirmed on pid ${P2:-?} -- check /proc read perms / goose_runner startup"
  fi
fi
