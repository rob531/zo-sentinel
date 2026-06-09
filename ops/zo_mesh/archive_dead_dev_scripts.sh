#!/usr/bin/env bash
# archive_dead_dev_scripts.sh -- Tier-3 sweep: retire superseded zo_mesh dev one-offs.
# REVERSIBLE: moves (does not delete) into ./archive/dev_scripts/. To undo:
#   mv /home/workspace/zo_mesh/archive/dev_scripts/* /home/workspace/zo_mesh/
#
# Safety basis (verified against the deployed code graph over the :8772 bus):
#   all six files have ZERO inbound edges of ANY relation -- nothing in the
#   live tree imports, calls, or references them. They are version-stamped
#   one-shots (v18/v19/v2.8/v3.3) long superseded by the current go.sh /
#   goose Phase-1 path.
set -euo pipefail
ROOT=/home/workspace/zo_mesh
DEST="$ROOT/archive/dev_scripts"
mkdir -p "$DEST"

DEAD=(
  apply_v18.py
  apply_v19.py
  upgrade_v18.py
  builder_ladder_test_v2.py
  test_go_v2.8_candidate.sh
  test_watchdog_v3.3.sh
)

moved=0
for f in "${DEAD[@]}"; do
  src="$ROOT/$f"
  if [[ -f "$src" ]]; then
    mv -v "$src" "$DEST/$f"
    moved=$((moved+1))
  else
    echo "[--] $f already gone (skip)"
  fi
done

echo
echo "[OK] archived $moved/${#DEAD[@]} dead dev scripts -> $DEST"
echo "     undo: mv $DEST/* $ROOT/"
