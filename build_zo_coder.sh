#!/usr/bin/env bash
# build_zo_coder.sh
# Creates the zo-sentinel-coder Ollama model from Modelfile.
# This bakes the ZO-SENTINEL coding rules into the model at the system level.
# The model is then used by the builder instead of raw llama3.2:3b.
#
# Run: bash /home/workspace/zo_sentinel/build_zo_coder.sh

set -e
GRN='\033[0;32m'; YLW='\033[0;33m'; BOLD='\033[1m'; NC='\033[0m'
ok(){  echo -e "  ${GRN}OK${NC} $1"; }
warn(){ echo -e "  ${YLW}!!${NC} $1"; }
hdr(){  echo -e "\n${BOLD}=== $1 ===${NC}"; }

hdr "1. Check Ollama is running"
curl -s http://localhost:11434/api/tags > /dev/null 2>&1 \
    && ok "Ollama running" \
    || { warn "Ollama not running - starting..."
         nohup ollama serve >> /home/workspace/logs/ollama.log 2>&1 &
         sleep 5; }

hdr "2. Check base model available"
if ollama list 2>/dev/null | grep -q 'llama3.2:3b'; then
    ok "llama3.2:3b available"
else
    warn "llama3.2:3b not found - pulling..."
    ollama pull llama3.2:3b
fi

hdr "3. Build zo-sentinel-coder model"
MODELFILE=/home/workspace/zo_sentinel/Modelfile.zo_coder
if [ ! -f "$MODELFILE" ]; then
    echo "ERROR: Modelfile not found at $MODELFILE"
    exit 1
fi

echo "  Building from $MODELFILE..."
ollama create zo-sentinel-coder -f "$MODELFILE"
ok "zo-sentinel-coder model created"

hdr "4. Verify model"
if ollama list 2>/dev/null | grep -q 'zo-sentinel-coder'; then
    ok "zo-sentinel-coder listed in ollama models"
else
    warn "Model not found in list - may still be building"
fi

hdr "5. Smoke test"
TEST_RESPONSE=$(ollama run zo-sentinel-coder \
    "Write a one-line Python function to POST to write_service at port 8772 with table='test' and rows={'key':'value'}" \
    2>/dev/null | head -5)
echo "  Test response preview:"
echo "  $TEST_RESPONSE" | head -3

# Check it doesn't contain the garbage patterns
if echo "$TEST_RESPONSE" | grep -qi 'write_service(' ; then
    warn "Model still outputting write_service() as function call"
elif echo "$TEST_RESPONSE" | grep -qi "'row':"; then
    warn "Model still using 'row' instead of 'rows'"
else
    ok "Smoke test patterns OK"
fi

hdr "6. Update builder config"
# Patch the builder to use zo-sentinel-coder as primary model
if grep -q 'OLLAMA_MODEL_PRIMARY' /home/workspace/zo_mesh/zo_sentinel_builder.py; then
    sed -i 's/OLLAMA_MODEL_PRIMARY  = .*/OLLAMA_MODEL_PRIMARY  = "zo-sentinel-coder"/' \
        /home/workspace/zo_mesh/zo_sentinel_builder.py
    ok "Builder updated: OLLAMA_MODEL_PRIMARY = zo-sentinel-coder"
else
    warn "Could not find OLLAMA_MODEL_PRIMARY in builder - manual update needed"
fi

hdr "7. Restart builder"
supervisorctl -c /etc/zo/supervisord-user.conf restart zo_sentinel_builder 2>/dev/null \
    && ok "Builder restarted with new model" \
    || warn "Builder restart failed - run zm go to restart"

hdr "DONE"
echo ""
echo "  zo-sentinel-coder is now the primary Ollama model for the builder."
echo "  All generation prompts will have the ZO-SENTINEL coding rules"
echo "  baked in at the model level, not just injected per-prompt."
echo ""
echo "  To verify: ollama list | grep zo-sentinel"
echo "  To test:   ollama run zo-sentinel-coder \"Write a heartbeat function\""
echo "  To update: edit Modelfile.zo_coder then re-run this script"
echo ""