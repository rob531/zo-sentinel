#!/usr/bin/env bash
# tomorrow_deploy.sh -- one-shot host deploy of the 2026-05-31 build-pipeline work.
#
# Run on ZoComputer:  bash /home/workspace/zo_sentinel/tools/tomorrow_deploy.sh
#
# Bundles: refresh -> write_service MERGE-INTO fix -> live go.sh patch (trio +
# keyed shim) -> breaker un-stall -> full restart -> post-checks.
# Soft-fails per step (reports + continues). Re-runnable: every patch/reset is
# idempotent. Enables NOTHING and spends NO paid credit -- the trio comes up
# DORMANT; flip a latch yourself when builds are steady.
#
# First run bootstrap: if this file isn't on the host yet, do the refresh once
# manually to fetch it, then run it (step 1's refresh is then a harmless re-run):
#   REFRESH_MODE=reset bash /home/workspace/shared/ops/init_zo_mesh_and_refresh_main.sh
set -uo pipefail
SENTINEL=/home/workspace/zo_sentinel
REFRESH=/home/workspace/shared/ops/init_zo_mesh_and_refresh_main.sh
GO_SH=/home/workspace/zo_mesh/go.sh
say(){ printf '\n\033[1m=== %s ===\033[0m\n' "$*"; }

say "1/5  Refresh repo -> origin/main  (#24 cost-gate, #25 hygiene, #26 patchers)"
REFRESH_MODE=reset bash "$REFRESH" || echo "  WARN: refresh failed -- check $REFRESH"

say "2/5  Patch write_service (DuckDB MERGE-INTO -> 500 fix)"
python3 "$SENTINEL/tools/patch_write_service_merge.py" || echo "  (already patched / anchor drift)"

say "3/5  Patch live zo_mesh/go.sh  (trio launch + keyed ladder_shim)"
python3 "$SENTINEL/tools/patch_go_sh.py" || echo "  (already patched / anchor drift)"

say "4/5  Un-stall the breaker  (clear stale quarantines + resume to half-open)"
( cd "$SENTINEL" \
    && python3 reset_breaker.py sweep-stale \
    && python3 reset_breaker.py half-open "tomorrow_deploy $(date -u +%FT%TZ)" ) \
  || echo "  WARN: breaker reset step errored"
# OPTIONAL -- if the breaker KEEPS re-tripping after this, the same files are
# failing cohorts. Retire them so the generator stops proposing rebuilds (edit
# the list to taste, then uncomment):
#   ( cd "$SENTINEL"; for f in tool_security_enrichment.py url_safety_enrichment.py \
#       snow_inbound_webhook_wiring.py; do
#       python3 reset_breaker.py retire "$f" "re-trips cohorts; retire"; done )

say "5/5  Restart the full stack  (runs the PATCHED go.sh: fixed write_service,"
say "      trio dormant, ladder_shim with keys)"
bash "$GO_SH" || echo "  WARN: go.sh returned non-zero"

say "Post-deploy checks"
echo "  :8772 write_service -> $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8772/health 2>/dev/null)"
echo "  :8796 ladder_shim   -> $(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8796/health 2>/dev/null)"
SP=$(pgrep -f ladder_shim.py 2>/dev/null | head -1)
if [ -n "$SP" ]; then
  G=$(tr '\0' '\n' < "/proc/$SP/environ" 2>/dev/null | grep -ic gemini)
  echo "  shim Gemini key     -> $([ "${G:-0}" -gt 0 ] && echo PRESENT || echo MISSING)"
fi
echo "  trio procs          -> ingestor/gov=$(pgrep -fc 'zo_sentinel.ingestor' 2>/dev/null), publisher=$(pgrep -fc 'zo_sentinel.publisher' 2>/dev/null)  (dormant)"
echo "  breaker state       -> $(cd "$SENTINEL" && python3 -c 'import json;print(json.load(open("gate_quality_state.json")).get("state"))' 2>/dev/null)"

say "Done. Trio is DORMANT. To enable when builds are steady:"
echo "  touch $SENTINEL/.ingestor_enabled        # or let the governor flip it"
echo "  # publisher: export PR_PUBLISHER_CLONE_DIR=<clone>; touch $SENTINEL/.pr_publisher_enabled"
