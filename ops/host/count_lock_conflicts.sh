#!/usr/bin/env bash
# count_lock_conflicts.sh -- empirical check of the goal metric (DuckDB write-lock
# conflicts). Baseline: DeepSeek 2026-06-05 analysis = 651 occurrences of
# "_duckdb.IOException: Could not set lock". This counts the signature across the
# CURRENT logs and -- the decisive number -- prints the NEWEST occurrence's
# timestamp. If the herd fix holds, the newest match is days old and today's
# count is 0.
#
# Read-only (grep only). Run ONE command:
#   bash /home/workspace/zo_sentinel/count_lock_conflicts.sh
set -uo pipefail
LOGDIR=/home/workspace/logs
SIG='Could not set lock|Conflicting lock|set lock on file|IOException.*[Ll]ock'
OUT=/home/workspace/logs/lock_conflict_audit.log
TODAY=$(date -u +%Y-%m-%d)
{
echo "=== duckdb lock-conflict audit $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo "baseline (2026-06-05 DeepSeek): 651"
echo
echo "[*] per-log match counts (current logs only; >0 shown):"
grep -rEc "$SIG" "$LOGDIR"/*.log 2>/dev/null | awk -F: '$2>0{print "  "$1": "$2; t+=$2} END{print "  ---- TOTAL (current logs): " t+0}'
echo
echo "[*] occurrences dated TODAY ($TODAY):"
grep -rE "$SIG" "$LOGDIR"/*.log 2>/dev/null | grep -c "$TODAY" || echo "  0"
echo
echo "[*] NEWEST lock-conflict timestamp found anywhere in current logs:"
grep -rhoE "$SIG" "$LOGDIR"/*.log >/dev/null 2>&1 && \
  grep -rE "$SIG" "$LOGDIR"/*.log 2>/dev/null \
    | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2}[ T][0-9]{2}:[0-9]{2}:[0-9]{2}' \
    | sort | tail -1 | sed 's/^/  /' \
  || echo "  (no matches at all in current logs)"
echo
echo "[*] most recent 3 matching lines (context):"
grep -rE "$SIG" "$LOGDIR"/*.log 2>/dev/null | tail -3 | cut -c1-160 | sed 's/^/  /' || echo "  (none)"
echo "=== done ==="
} 2>&1 | tee "$OUT"
