#!/usr/bin/env bash
# reseed_zo_mesh_gatetest.sh -- re-seed the zo_mesh code graph from scratch and
# CONFIRM the archive/ exclusion gates hold. The 6 dead dev scripts now live
# under archive/dev_scripts/; a re-index must NOT bring them back.
#
#   Gate 1 (.graphifyignore): graphify must not emit archive/ into graph.json.
#   Gate 2 (loader _drop_archived): even if it did, the bus load drops them.
#
# Run ONE command:  bash /home/workspace/zo_mesh/reseed_zo_mesh_gatetest.sh
LOG=/home/workspace/logs/reseed_zo_mesh_gatetest.log
MESH=/home/workspace/zo_mesh
SENT=/home/workspace/zo_sentinel
GRAPH="$MESH/graphify-out/graph.json"
{
echo "=== reseed_zo_mesh gatetest start $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="

# 0. Pull the loader that has _drop_archived (PR #111). zo_mesh's own
#    .graphifyignore lives under $MESH and is untouched by this reset.
cd "$SENT" || { echo "FATAL: cannot cd $SENT"; exit 1; }
git fetch origin main -q && git reset --hard origin/main -q
echo "  zo_sentinel HEAD: $(git rev-parse --short HEAD)"
echo "  .graphifyignore present in zo_mesh: $([ -f "$MESH/.graphifyignore" ] && echo YES || echo NO)"

# 1. Re-index zo_mesh (code-only graphify update; honors .graphifyignore).
echo "--- graphify update $MESH ---"
python3 "$SENT/tools/index_graph.py" --root "$MESH"

# 2. GATE 1 -- archive/ must be absent from the freshly-built graph.json.
G1=$(grep -c "archive/dev_scripts" "$GRAPH" 2>/dev/null || echo 0)
echo "  GATE1 graphify: 'archive/dev_scripts' occurrences in graph.json = $G1 (expect 0)"
for f in apply_v18.py apply_v19.py upgrade_v18.py builder_ladder_test_v2.py test_go_v2.8_candidate.sh test_watchdog_v3.3.sh; do
  c=$(grep -c "$f" "$GRAPH" 2>/dev/null || echo 0)
  echo "    $f in graph.json = $c (expect 0)"
done

# 3. Re-seed the bus (clears zo_mesh rows first via --keep, then _drop_archived).
echo "--- load_graph_to_bus --repo zo_mesh --keep ---"
python3 "$SENT/tools/load_graph_to_bus.py" --repo zo_mesh --graph "$GRAPH" --keep --purge-old

echo "=== reseed_zo_mesh gatetest done $(date -u +%Y-%m-%dT%H:%M:%SZ) ==="
} 2>&1 | tee -a "$LOG"
