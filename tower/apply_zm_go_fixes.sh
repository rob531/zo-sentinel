#!/usr/bin/env bash
# apply_zm_go_fixes.sh -- one-shot runner for all 2026-05-28 zm go fixes.
#
# Run with: bash /home/workspace/apply_zm_go_fixes.sh
#
# Wraps the four operational steps:
#   1. Patch go.sh section 18 bootstrap-timeout 60s -> 120s
#   2. Patch goose_runner.py log() to drop the redundant print()
#      (kills the double-line spam in goose_runner.log)
#   3. Restart threat_intel_ingestor under daemon_wrapper (picks up the
#      new code: dropped evidence(100), added source field)
#   4. Run OOM diagnostic so we have evidence for the WorldAgent kill
#
# Idempotent: safe to re-run. Each step prints OK / SKIP / FAIL.

set -u
echo "=== apply_zm_go_fixes.sh @ $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
echo

step() { echo; echo "--- $1 ---"; }
ok()   { echo "  [OK] $1"; }
warn() { echo "  [!!] $1"; }

step "1/4  patch go.sh bootstrap timeout (60s -> 120s)"
if bash /home/workspace/patch_go_sh_bootstrap_timeout.sh; then
  ok "go.sh patched (or already current)"
else
  warn "patch_go_sh_bootstrap_timeout.sh failed (exit $?)"
fi

step "2/4  patch goose_runner.py double-log (drop redundant print)"
if bash /home/workspace/patch_goose_runner_double_log.sh; then
  ok "goose_runner.py patched (or already current)"
  if pgrep -af 'python.* goose_runner\.py$' > /dev/null 2>&1; then
    pkill -f 'python.* goose_runner\.py$' 2>/dev/null || true
    sleep 4
    if pgrep -af 'goose_runner\.py' > /dev/null 2>&1; then
      ok "goose_runner respawned by daemon_wrapper.sh"
    else
      warn "goose_runner not visible after restart; daemon_wrapper may need re-nohup"
    fi
  else
    warn "goose_runner wasn't running; will pick up new code on next zm go"
  fi
else
  warn "patch_goose_runner_double_log.sh failed (exit $?)"
fi

step "3/4  restart threat_intel_ingestor with new code"
bash /home/workspace/restart_threat_intel.sh

step "4/4  run OOM diagnostic"
bash /home/workspace/diagnose_oom.sh > /dev/null
OOM_REPORT=$(ls -t /home/workspace/logs/diagnose_oom.*.txt 2>/dev/null | head -1)
if [[ -n "$OOM_REPORT" ]]; then
  ok "OOM report: $OOM_REPORT"
  echo
  echo "  Highlights:"
  grep -E 'memory.max|memory.events|Killed|OOM|out of memory' "$OOM_REPORT" 2>/dev/null | head -8 | sed 's/^/    /'
else
  warn "OOM diagnostic didn't produce a report"
fi

step "verification"
echo "  threat_intel_ingestor running:"
pgrep -af threat_intel_ingestor.py 2>/dev/null | sed 's/^/    /' | head -3
echo
echo "  goose_runner running:"
pgrep -af goose_runner.py 2>/dev/null | sed 's/^/    /' | head -3
echo
echo "  recent write_service.log (look for absence of evidence(100) errors):"
tail -10 /home/workspace/logs/write_service.log 2>/dev/null | sed 's/^/    /'
echo
echo "=== done ==="
