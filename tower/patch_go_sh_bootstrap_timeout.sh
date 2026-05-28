#!/usr/bin/env bash
# patch_go_sh_bootstrap_timeout.sh -- bump section 18's wait_for_ws gate
# in /home/workspace/zo_mesh/go.sh from 60s (20 iterations) to 120s (40),
# matching the new full_schema_bootstrap.py default.
#
# One-shot, idempotent: re-running is a no-op. Backup made first.

set -euo pipefail

GO=/home/workspace/zo_mesh/go.sh
TS=$(date -u +%Y%m%d_%H%M%SZ)
BAK="$GO.bak.bootstrap_timeout.$TS"

echo "=== patch_go_sh_bootstrap_timeout.sh ==="
echo "target: $GO"
echo "backup: $BAK"

if [[ ! -f "$GO" ]]; then
  echo "ERROR: $GO not found"
  exit 2
fi

if grep -q 'seq 1 40' "$GO" 2>/dev/null && grep -q 'Waiting up to 120s' "$GO" 2>/dev/null; then
  echo "Already patched. Nothing to do."
  exit 0
fi

sudo cp -p "$GO" "$BAK"
echo "backup written"

sudo sed -i 's/for i in \$(seq 1 20); do/for i in $(seq 1 40); do/' "$GO"
sudo sed -i 's/Waiting up to 60s for write_service/Waiting up to 120s for write_service/' "$GO"
sudo sed -i 's|warn "write_service not ready -- skipping bootstrap"|warn "write_service not ready after 120s -- running bootstrap anyway; it has its own 120s gate"; python3 $SENTINEL/full_schema_bootstrap.py 2>\&1 | tee $LOGS/bootstrap.log | tail -3|' "$GO"

echo "--- verification ---"
grep -n 'seq 1 40\|Waiting up to 120s\|running bootstrap anyway' "$GO" | head -5
echo
echo "=== done ==="
echo "To revert: sudo cp -p $BAK $GO"
