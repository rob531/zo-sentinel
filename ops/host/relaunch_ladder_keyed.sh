#!/usr/bin/env bash
# relaunch_ladder_keyed.sh -- rewire ladder_shim off the DEAD key_hydrator.
# Keys live under NON-canonical names; map each to the canonical env the shim wants:
#   gemini    : /root/.zo_secrets 'RcGeminiAPIKey'        -> GEMINI_API_KEY + RcGeminiAPIKey
#   minimax   : /root/.zo_secrets 'MINIMAX_API_KEY'       -> MINIMAX_API_KEY
#   anthropic : 'PWD_ZO_COMPUTER_ANTHROPICAPI' ($30 credits) -> ANTHROPIC_API_KEY,
#               tried file-exact -> file-substring -> secretless-ai (multi-source).
# LEAST-PRIVILEGE: only these 3. SAFE: pre-verify (PRESENT/ABSENT, never values)
# before killing the shim; FALL BACK to bare launch if the keyed start fails.
# Not durable across reboot (go.sh launches bare) -- wiring go.sh is the follow-up.
#
# Run ONE command:  bash /home/workspace/zo_sentinel/relaunch_ladder_keyed.sh
set -uo pipefail
SENT=/home/workspace/zo_sentinel; LOGS=/home/workspace/logs
WRAP=$SENT/ladder_shim_keyed.sh
SECRETS=/root/.zo_secrets
SL=/home/workspace/node_modules/.bin/secretless-ai

_name() { grep -oiE "^(export[[:space:]]+)?[A-Za-z0-9_]*($1)[A-Za-z0-9_]*=" "$SECRETS" 2>/dev/null | head -1 | sed -E 's/^(export[[:space:]]+)?//; s/=$//'; }
_anth_sl() { [ -x "$SL" ] && "$SL" run --only ANTHROPIC_API_KEY -- printenv ANTHROPIC_API_KEY 2>/dev/null | grep -q .; }

# 1. write the wrapper (gemini+minimax from file; anthropic multi-source)
cat > "$WRAP" <<'EOF'
#!/usr/bin/env bash
# ladder_shim launcher (key_hydrator dead). Values never printed.
S=/root/.zo_secrets; SL=/home/workspace/node_modules/.bin/secretless-ai
_val() { grep -oiE "^(export[[:space:]]+)?[A-Za-z0-9_]*($1)[A-Za-z0-9_]*=.*" "$S" 2>/dev/null | head -1 | sed -E "s/^[^=]*=//; s/^[\"']//; s/[\"'][[:space:]]*$//"; }
if [ -r "$S" ]; then
  M=$(_val 'minimax'); [ -n "$M" ] && export MINIMAX_API_KEY="$M"
  G=$(_val 'gemini');  [ -n "$G" ] && { export GEMINI_API_KEY="$G"; export RcGeminiAPIKey="$G"; }
fi
# anthropic (PWD_ZO_COMPUTER_ANTHROPICAPI): file-exact -> file-substring -> secretless-ai
A=$(_val 'PWD_ZO_COMPUTER_ANTHROPICAPI')
[ -z "$A" ] && A=$(_val 'anthropic')
[ -z "$A" ] && [ -x "$SL" ] && A=$("$SL" run --only ANTHROPIC_API_KEY -- printenv ANTHROPIC_API_KEY 2>/dev/null)
[ -z "$A" ] && [ -x "$SL" ] && A=$("$SL" run --only PWD_ZO_COMPUTER_ANTHROPICAPI -- printenv PWD_ZO_COMPUTER_ANTHROPICAPI 2>/dev/null)
[ -n "$A" ] && export ANTHROPIC_API_KEY="$A"
exec python3 /home/workspace/zo_sentinel/ladder_shim.py
EOF
chmod +x "$WRAP"
echo "wrote wrapper: $WRAP"

# 2. PRE-VERIFY (no restart; no values -- report which SOURCE yields each)
[ -r "$SECRETS" ] || { echo "[abort] $SECRETS unreadable"; exit 3; }
GN=$(_name 'gemini')
echo "[pre-verify]:"
echo "    gemini    -> ${GN:-(none)}  (file)"
AS=""
if [ -n "$(_name 'PWD_ZO_COMPUTER_ANTHROPICAPI')" ]; then AS="file:PWD_ZO_COMPUTER_ANTHROPICAPI";
elif [ -n "$(_name 'anthropic')" ]; then AS="file:anthropic-substring";
elif _anth_sl; then AS="secretless-ai"; fi
echo "    anthropic -> ${AS:-(none)}"
if [ -z "$GN" ] && [ -z "$AS" ]; then echo "[abort] neither gemini nor anthropic resolved -- not restarting."; exit 2; fi

# 3. relaunch via wrapper, fallback-to-bare
echo "[relaunch] restarting ladder_shim..."
pkill -f '[l]adder_shim.py' 2>/dev/null; sleep 2
nohup bash "$WRAP" >> "$LOGS/ladder_shim.log" 2>&1 &
sleep 6
if pgrep -f '[l]adder_shim.py' >/dev/null; then
  echo "[ok] ladder_shim UP (pid $(pgrep -f '[l]adder_shim.py' | head -1))"
else
  echo "[FALLBACK] keyed start failed -- reverting to bare launch"
  nohup python3 "$SENT/ladder_shim.py" >> "$LOGS/ladder_shim.log" 2>&1 &
  sleep 3; echo "    bare pid: $(pgrep -f '[l]adder_shim.py' | head -1 || echo NONE)"
fi
echo "--- ladder_shim.log tail ---"; tail -16 "$LOGS/ladder_shim.log"
echo "=== done -- no 'RcGeminiAPIKey unresolved' => gemini + critical(anthropic) rungs live ==="
