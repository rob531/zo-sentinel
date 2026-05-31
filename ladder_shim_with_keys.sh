#!/usr/bin/env bash
# Launch ladder_shim.py with the LLM keys resolved into ITS env via the canonical
# ZoComputer resolver (key_hydrator), so escalation.py reaches the Gemini rung,
# not just MiniMax.
#
# Why this is needed (see docs/SECRETS.md + zo_mesh/key_hydrator.py):
# Modal injects MINIMAX_API_KEY under its canonical name, so every process -- incl
# the shim -- inherits it (rung 0 works). GEMINI_API_KEY is a Modal *alias* that
# key_hydrator resolves to the canonical name PER PROCESS. The shim never ran that
# resolution, so escalation.py sees "RcGeminiAPIKey not set" and the ladder 502s
# every build routed above rung 0 (e.g. complexity=medium). `--get` runs the same
# resolver and prints one key; we capture it into the env, then exec the shim.
KH=/home/workspace/zo_mesh/key_hydrator.py
for k in MINIMAX_API_KEY GEMINI_API_KEY ANTHROPIC_API_KEY; do
  v="$(python3 "$KH" --get "$k" 2>/dev/null)" && [[ -n "$v" ]] && export "$k=$v"
done
# escalation.py checks RcGeminiAPIKey first, then GEMINI_API_KEY -- mirror it.
[[ -n "${GEMINI_API_KEY:-}" ]] && export RcGeminiAPIKey="${RcGeminiAPIKey:-$GEMINI_API_KEY}"
exec python3 /home/workspace/zo_sentinel/ladder_shim.py
