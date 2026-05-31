#!/usr/bin/env bash
# ladder_shim_with_keys.sh -- launch ladder_shim.py with EVERY provider key in
# its environment, so escalation.py can reach the Gemini/Zo rungs, not just
# MiniMax.
#
# Root cause this fixes: the ladder_shim process inherits MINIMAX_API_KEY (rung 0
# works) but NOT the Gemini key, so escalation.py's Gemini/Gemma adapters fail
# "RcGeminiAPIKey not set" and the ladder exhausts into a 502 -- which 502s every
# build routed above rung 0 (e.g. complexity=medium -> zo-ladder-medium). The
# key is NOT missing: the vault has it under both GEMINI_API_KEY and
# RcGeminiAPIKey, and escalation.py checks both -- it just never reaches the
# shim's env. This wrapper closes that gap at launch time.
#
# Usage:
#   KEYS_ENV=/path/to/host/keys.env  bash ladder_shim_with_keys.sh
# Find the host key-file (the one that already feeds MINIMAX) with:
#   grep -rlI MINIMAX_API_KEY /home/workspace /etc/zo ~ 2>/dev/null
#
# Note: the Zo rungs (10-15) fail 402/429 = billing/throttle, NOT keys -- this
# only unblocks Gemini. After this: low->MiniMax, medium->Gemini, high->coworker.
set -uo pipefail

SENTINEL="${SENTINEL:-/home/workspace/zo_sentinel}"
SHIM="$SENTINEL/ladder_shim.py"
KEYS_ENV="${KEYS_ENV:-/home/workspace/.keys.env}"

load_keys() {
  if [[ ! -f "$KEYS_ENV" ]]; then
    echo "WARN: KEYS_ENV=$KEYS_ENV not found; shim uses inherited env only" >&2
    return
  fi
  local raw trimmed k v
  while IFS= read -r raw || [[ -n "$raw" ]]; do
    raw="${raw%$'\r'}"                                    # strip CR
    raw="$(printf '%s' "$raw" | sed 's/^\xEF\xBB\xBF//')"  # strip UTF-8 BOM
    trimmed="${raw#"${raw%%[![:space:]]*}"}"             # ltrim
    [[ -z "$trimmed" || "$trimmed" == \#* || "$trimmed" != *=* ]] && continue
    k="${trimmed%%=*}"; v="${trimmed#*=}"
    k="$(printf '%s' "$k" | tr -d '[:space:]')"
    v="${v#\"}"; v="${v%\"}"; v="${v#\'}"; v="${v%\'}"
    [[ -n "$k" ]] && export "$k=$v"
  done < "$KEYS_ENV"
}
load_keys

# escalation.py tries RcGeminiAPIKey first, then GEMINI_API_KEY -- mirror so
# whichever name the vault file used satisfies the adapter.
[[ -n "${GEMINI_API_KEY:-}" && -z "${RcGeminiAPIKey:-}" ]] && export RcGeminiAPIKey="$GEMINI_API_KEY"
[[ -n "${RcGeminiAPIKey:-}" && -z "${GEMINI_API_KEY:-}" ]] && export GEMINI_API_KEY="$RcGeminiAPIKey"

# Masked confirmation -- names + length only, NEVER the secret value.
echo "ladder_shim key hydration:" >&2
for v in MINIMAX_API_KEY RcGeminiAPIKey GEMINI_API_KEY ZO_CLIENT_IDENTITY_TOKEN; do
  val="${!v:-}"
  if [[ -n "$val" ]]; then echo "  $v: SET (len=${#val})" >&2; else echo "  $v: MISSING" >&2; fi
done

exec python3 "$SHIM"
