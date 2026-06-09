#!/usr/bin/env bash
# prune_archived_graph_nodes.sh -- evict the now-archived zo_mesh dev-script
# nodes from the deployed code graph so the knowledge layer matches disk.
#
# Safe basis (verified over the :8772 bus before staging this):
#   22 code_nodes across the 6 archived files; 26 edges, ALL internal to the
#   set (0 inbound edges from any live node). Nothing live references them.
#
# Reversible: re-running the zo_mesh graph re-seed at the current commit
#   re-adds whatever is on disk. (The archived copies now live under
#   archive/dev_scripts/ -- excluded from graphify via .graphifyignore.)
set -euo pipefail
BUS=http://127.0.0.1:8772
FILES="'apply_v18.py','apply_v19.py','upgrade_v18.py','builder_ladder_test_v2.py','test_go_v2.8_candidate.sh','test_watchdog_v3.3.sh'"
SEL="SELECT id FROM code_nodes WHERE repo='zo_mesh' AND source_file IN ($FILES)"

echo "[*] before:"
curl -s --max-time 20 -X POST "$BUS/query" -H 'Content-Type: application/json' \
  -d "{\"sql\":\"SELECT COUNT(*) AS nodes FROM ($SEL)\"}"; echo

echo "[*] deleting edges touching the dead set..."
curl -s --max-time 30 -X POST "$BUS/execute" -H 'Content-Type: application/json' \
  -d "{\"sql\":\"DELETE FROM code_edges WHERE src IN ($SEL) OR dst IN ($SEL)\"}"; echo

echo "[*] deleting the 22 dead nodes..."
curl -s --max-time 30 -X POST "$BUS/execute" -H 'Content-Type: application/json' \
  -d "{\"sql\":\"DELETE FROM code_nodes WHERE repo='zo_mesh' AND source_file IN ($FILES)\"}"; echo

echo "[*] after (expect 0):"
curl -s --max-time 20 -X POST "$BUS/query" -H 'Content-Type: application/json' \
  -d "{\"sql\":\"SELECT COUNT(*) AS remaining_nodes FROM ($SEL)\"}"; echo

echo "[OK] graph pruned to match disk."
