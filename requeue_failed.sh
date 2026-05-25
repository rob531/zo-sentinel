#!/usr/bin/env bash
# requeue_failed.sh
# Removes .done.json markers for directives whose output files
# contain InferenceRouter error strings, forcing the builder to rebuild them.
# Run once after credits are restored.

SENTINEL=/home/workspace/zo_sentinel
DIRS=$SENTINEL/directives

echo "=== ZO-SENTINEL: Requeue Failed Builds ==="

# Files that are known to contain error strings (285 bytes each)
for f in \
  mcp_scanner.py \
  signal_analyser.py \
  trust_synthesiser.py \
  approval_workflow.py \
  schema_v2.py \
  known_threats.py \
  policy_engine.py \
  rug_pull_monitor.py \
  registry_api.py \
  schema.py
do
  fpath="$SENTINEL/$f"
  if [ -f "$fpath" ]; then
    size=$(wc -c < "$fpath")
    if [ "$size" -lt 400 ]; then
      echo "  Removing poisoned file: $f ($size bytes)"
      rm "$fpath"
    else
      echo "  OK: $f ($size bytes)"
    fi
  fi
done

# Remove .done.json markers to allow re-processing
echo ""
echo "Removing .done markers..."
for done_f in $DIRS/*.done.json; do
  [ -f "$done_f" ] || continue
  # Restore to .json for reprocessing
  original="${done_f/.done.json/.json}"
  mv "$done_f" "$original"
  echo "  Restored: $(basename $original)"
done

# Clear idempotency registry entries with 'failed' or 'smoke_fail' status
if [ -f "$SENTINEL/.build_registry.json" ]; then
  python3 - << 'PYEOF'
import json
path = '/home/workspace/zo_sentinel/.build_registry.json'
try:
    reg = json.loads(open(path).read())
    cleaned = {k: v for k, v in reg.items() if v.get('status') == 'ok'}
    removed = len(reg) - len(cleaned)
    open(path, 'w').write(json.dumps(cleaned, indent=2))
    print(f'  Registry: kept {len(cleaned)} ok entries, removed {removed} failed entries')
except Exception as e:
    print(f'  Registry clean error: {e}')
PYEOF
fi

echo ""
echo "Done. Restart builder to process: supervisorctl -c /etc/zo/supervisord-user.conf restart zo_sentinel_builder"
echo "Or: zm go"