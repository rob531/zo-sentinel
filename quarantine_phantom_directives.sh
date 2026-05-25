#!/usr/bin/env bash
# quarantine_phantom_directives.sh
# Moves chronically-failing .done.json directives that auto_restore keeps
# re-queueing into a /stalled/ folder so they stop consuming build cycles.
#
# Phantom directives confirmed:
#   auto_dependency_resolver  -- scoping bug loop, 50+ cycles wasted
#   arcade_toolbench_ingestor -- 50+ BUILD_STATE entries = same loop
#   graphql_schema            -- graphql-core C extension, uninstallable
#   ui/approval_workflow.jsx  -- JSX generation fails with 49KB context
#
# After this script runs:
#   - These directives will NOT be auto-restored
#   - Their current files on disk remain (auto_dependency_resolver.py etc exist and pass smoke)
#   - The builder queue clears and can process new directives
#
# Usage: bash /home/workspace/zo_sentinel/quarantine_phantom_directives.sh

DIRS=/home/workspace/zo_sentinel/directives
STALLED=$DIRS/stalled
mkdir -p $STALLED

echo "=== ZO-SENTINEL: Quarantine phantom directives ==="

quarantine() {
  local pattern="$1"
  local count=0
  for f in $DIRS/*${pattern}*.json $DIRS/*${pattern}*.done.json; do
    [ -f "$f" ] || continue
    echo "  Quarantine: $(basename $f)"
    mv "$f" "$STALLED/"
    count=$((count + 1))
  done
  echo "  -> Moved $count files for pattern: $pattern"
}

# Quarantine the four known phantom loops
quarantine "auto_dependency_resolver"
quarantine "arcade_toolbench"
quarantine "graphql_schema"
quarantine "approval_workflow"

# Also quarantine RETIRED_* which are still being auto-restored
for f in $DIRS/RETIRED_* $DIRS/*RETIRED*; do
  [ -f "$f" ] || continue
  echo "  Quarantine RETIRED: $(basename $f)"
  mv "$f" "$STALLED/"
done

echo ""
echo "Remaining active directives:"
ls $DIRS/*.json 2>/dev/null | head -20

echo ""
echo "Done. Run zm go to start fresh build cycles."