#!/usr/bin/env bash
# Launch ladder_shim.py with the host's vault keys exported into its env, so
# escalation.py reaches the Gemini rung (not just MiniMax). The shim normally
# inherits MINIMAX_API_KEY but not the Gemini key -> "RcGeminiAPIKey not set" ->
# the ladder 502s every build routed above rung 0 (e.g. complexity=medium).
#
# The whole trick: `set -a` auto-exports every var that `source` assigns.
# escalation.py checks RcGeminiAPIKey OR GEMINI_API_KEY, so no name-mirroring is
# needed -- the key just has to be in the keyfile and reach this process.
KEYS="${KEYS:-$HOME/.zo_secrets.env}"     # host key=value file (has the vault keys)
[[ -f "$KEYS" ]] && { set -a; . "$KEYS"; set +a; } || echo "WARN: $KEYS missing" >&2
exec python3 /home/workspace/zo_sentinel/ladder_shim.py
