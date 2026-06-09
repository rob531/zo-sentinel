#!/usr/bin/env bash
# probe_secretless.sh -- READ-ONLY probe of the secretless-ai key path, to decide
# how to rewire ladder_shim off the dead key_hydrator. Restarts NOTHING. NEVER
# prints a secret value -- only PRESENT/ABSENT (the value is piped to grep -q and
# discarded, never echoed).
#
# NOTE: section [3] lists /root/.zo_secrets key NAMES; scope it tightly to the
# providers you care about rather than dumping the whole inventory.
#
# Run ONE command:  bash /home/workspace/zo_sentinel/probe_secretless.sh
set -uo pipefail
SL=/home/workspace/node_modules/.bin/secretless-ai
LOG=/home/workspace/logs/probe_secretless.log
{
echo "=== probe_secretless $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

echo "[1] secretless-ai binary:"
if [ -x "$SL" ]; then echo "    FOUND: $SL"; else echo "    MISSING at $SL"; command -v secretless-ai && SL=$(command -v secretless-ai) && echo "    (on PATH: $SL)" || echo "    not on PATH either"; fi

echo "[2] key availability via secretless-ai (PRESENT/ABSENT only -- no values printed):"
for k in MINIMAX_API_KEY GEMINI_API_KEY ANTHROPIC_API_KEY RcGeminiAPIKey; do
  if "$SL" run --only "$k" -- printenv "$k" 2>/dev/null | grep -q .; then
    echo "    $k: PRESENT"
  else
    echo "    $k: ABSENT"
  fi
done

echo "[3] LLM-provider key NAMES in /root/.zo_secrets (values redacted; scoped to providers):"
if [ -r /root/.zo_secrets ]; then
  grep -oiE '^(export[[:space:]]+)?[A-Za-z0-9_]+=' /root/.zo_secrets 2>/dev/null \
    | sed -E 's/^(export[[:space:]]+)?//; s/=$//' \
    | grep -iE 'gemini|anthropic|claude|google|minimax|gpt|openai' \
    | sort -u | sed 's/^/    has: /'
else
  echo "    /root/.zo_secrets not readable as this user (expected; secretless-ai brokers it)"
fi

echo "[4] current ladder_shim launch (how it starts now):"
pgrep -af '[l]adder_shim.py' || echo "    ladder_shim not running?!"

echo "[5] keyed wrapper present?"
ls -la /home/workspace/zo_sentinel/ladder_shim_with_keys.sh 2>/dev/null || echo "    no ladder_shim_with_keys.sh"

echo "=== done ==="
} 2>&1 | tee "$LOG"
