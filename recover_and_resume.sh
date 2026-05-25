#!/usr/bin/env bash
# recover_and_resume.sh  v1.0  (2026-04-16)
#
# Master recovery script for ZO-SENTINEL builder.
# Encodes the 5-step sequence from DEEP_PONDER_v4_LOOP4_FINAL.md.
#
# WHAT THIS FIXES:
#   Bug 1: _builds_this_session UnboundLocalError -> infinite directive loop
#   Bug 2: mark_directive_done in try block -> loop survives exceptions
#   Bug 3: BUILD_STATE.md deduplication -> prompt bloat killing MiniMax output
#   Bug 4: Phantom loop directives (auto_dependency_resolver, arcade_toolbench)
#
# AFTER THIS RUNS:
#   - Builder starts fresh with a clean ~3KB BUILD_STATE.md
#   - First cycle writes config_validator.py via write_raw (queue clears cleanly)
#   - Subsequent cycles: 'No pending directives' (healthy quiet state)
#   - watch.py is available: python3 /home/workspace/zo_sentinel/watch.py
#
# Usage:
#   bash /home/workspace/zo_sentinel/recover_and_resume.sh
#
# Idempotent: safe to run multiple times.

set -e
cd /home/workspace

echo ""
echo "======================================================"
echo " ZO-SENTINEL Builder Recovery  v1.0  2026-04-16"
echo "======================================================"
echo ""

# -- Step 1: Compress BUILD_STATE.md ------------------------------------
echo "[1/5] Compressing BUILD_STATE.md..."
if python3 /home/workspace/zo_sentinel/compress_build_state.py; then
  echo "      OK"
else
  echo "      WARN: compress failed (may already be clean)"
fi

# -- Step 2: Apply builder patch ----------------------------------------
echo "[2/5] Applying builder patch (v1.9.2 -> v1.9.3)..."
if python3 /home/workspace/zo_sentinel/fix_builder_v193.py; then
  echo "      OK"
else
  echo "      WARN: patch failed (may already be applied)"
fi

# -- Step 3: Quarantine phantom loop directives -------------------------
echo "[3/5] Quarantining phantom directives..."
bash /home/workspace/zo_sentinel/quarantine_phantom_directives.sh 2>&1 | grep -v '^$'
echo "      OK"

# -- Step 4: Kill any stale builder process -----------------------------
echo "[4/5] Stopping any stale builder process..."
if pkill -f zo_sentinel_builder.py 2>/dev/null; then
  echo "      Killed stale builder process"
  sleep 2
else
  echo "      Builder was not running (OK)"
fi

# -- Step 5: Start builder via zm go ------------------------------------
echo "[5/5] Starting builder via zm go..."
if command -v zm &>/dev/null; then
  zm go
  echo "      OK (zm go executed)"
elif command -v supervisorctl &>/dev/null; then
  supervisorctl -c /etc/zo/supervisord-user.conf restart zo_sentinel_builder 2>/dev/null || \
    nohup python3 /home/workspace/zo_mesh/zo_sentinel_builder.py \
      >> /home/workspace/logs/zo_sentinel_builder.log 2>&1 &
  echo "      OK (supervisorctl or nohup)"
else
  nohup python3 /home/workspace/zo_mesh/zo_sentinel_builder.py \
    >> /home/workspace/logs/zo_sentinel_builder.log 2>&1 &
  echo "      OK (nohup, PID $!)"
fi

echo ""
echo "======================================================"
echo " Recovery complete."
echo " Wait 5-10 minutes then verify:"
echo ""
echo "   tail -30 /home/workspace/logs/zo_sentinel_builder.log"
echo "   # Healthy: 'config_validator.py ... OK' then 'No pending directives'"
echo "   # Unhealthy: still looping on same task"
echo ""
echo " Optional: launch live dashboard:"
echo "   python3 /home/workspace/zo_sentinel/watch.py --interval 10"
echo "======================================================"
echo ""