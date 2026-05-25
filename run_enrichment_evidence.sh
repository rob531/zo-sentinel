#!/usr/bin/env bash
# run_enrichment_evidence.sh
# ----------------------------------------------------------------------------
# One-shot: apply supply_chain scale fix (if needed), harness all three
# enrichments (supply_chain, domain_trust, community_signal), then emit
# the evidence query output. Everything routes to a single log file so
# Claude can read the whole run's results in one pass.
#
# Output: /home/workspace/logs/enrichment_evidence.txt
# ----------------------------------------------------------------------------
set -uo pipefail

SENTINEL=/home/workspace/zo_sentinel
LOG=/home/workspace/logs/enrichment_evidence.txt

GRN=$'\033[0;32m'; YLW=$'\033[0;33m'; RED=$'\033[0;31m'; NC=$'\033[0m'
ok()   { printf "  %s[OK]%s %s\n" "$GRN" "$NC" "$*"; }
warn() { printf "  %s[!]%s %s\n"  "$YLW" "$NC" "$*"; }
bad()  { printf "  %s[X]%s %s\n"  "$RED" "$NC" "$*"; }

mkdir -p /home/workspace/logs
: > "$LOG"

echo "=== STAGE 1: supply_chain scale fix ===" | tee -a "$LOG"
if bash $SENTINEL/patch_supply_chain_scale.sh 2>&1 | tee -a "$LOG"; then
    ok "scale patch step OK"
else
    warn "scale patcher returned non-zero, continuing anyway"
fi
echo | tee -a "$LOG"

echo "=== STAGE 2: harness supply_chain_enrichment ===" | tee -a "$LOG"
python3 $SENTINEL/enrichment_harness.py \
    --enrichment $SENTINEL/supply_chain_enrichment.py \
    --runs 3 \
    --sample-size 20 2>&1 | tee -a "$LOG"
echo | tee -a "$LOG"

echo "=== STAGE 3: harness domain_trust_enrichment ===" | tee -a "$LOG"
python3 $SENTINEL/enrichment_harness.py \
    --enrichment $SENTINEL/domain_trust_enrichment.py \
    --runs 3 \
    --sample-size 20 2>&1 | tee -a "$LOG"
echo | tee -a "$LOG"

echo "=== STAGE 4: harness community_signal_enrichment ===" | tee -a "$LOG"
python3 $SENTINEL/enrichment_harness.py \
    --enrichment $SENTINEL/community_signal_enrichment.py \
    --runs 3 \
    --sample-size 20 2>&1 | tee -a "$LOG"
echo | tee -a "$LOG"

echo "=== STAGE 5: evidence verdict per enrichment ===" | tee -a "$LOG"
# Run the evidence SQL via write_service /query and pretty-print the output.
python3 - <<'PYEOF' 2>&1 | tee -a "$LOG"
import json
import requests
SQL = open("/home/workspace/zo_sentinel/enrichment_evidence.sql").read()
# Strip SQL comments so /query is happy
clean = "\n".join(l for l in SQL.splitlines() if not l.strip().startswith("--"))
r = requests.post("http://127.0.0.1:8772/query",
                  json={"sql": clean}, timeout=30)
if r.status_code != 200:
    print("QUERY FAILED:", r.status_code, r.text[:200])
else:
    rows = r.json().get("rows", [])
    if not rows:
        print("No enrichment evidence rows yet")
    else:
        # Pretty table
        headers = list(rows[0].keys())
        widths = {h: max(len(h), max(len(str(r[h])) for r in rows)) for h in headers}
        line = "  " + "  ".join(h.ljust(widths[h]) for h in headers)
        print(line)
        print("  " + "  ".join("-" * widths[h] for h in headers))
        for r in rows:
            print("  " + "  ".join(str(r[h]).ljust(widths[h]) for h in headers))
PYEOF

echo | tee -a "$LOG"
echo "=== STAGE 6: raw enrichment row counts by (enrichment, run) ===" | tee -a "$LOG"
python3 - <<'PYEOF' 2>&1 | tee -a "$LOG"
import requests
r = requests.post("http://127.0.0.1:8772/query",
                  json={"sql":
                    "SELECT enrichment_name, run_id, COUNT(*) AS rows, "
                    "COUNT(DISTINCT score) AS distinct_scores, "
                    "ROUND(MIN(score),1) AS lo, ROUND(MAX(score),1) AS hi "
                    "FROM mcp_signal_enrichments "
                    "GROUP BY enrichment_name, run_id "
                    "ORDER BY enrichment_name, run_id"}, timeout=30)
if r.status_code == 200:
    for row in r.json().get("rows", []):
        print(f"  {row['enrichment_name']:<32} {row['run_id']:<30} "
              f"rows={row['rows']:>3}  distinct={row['distinct_scores']:>3}  "
              f"range=[{row['lo']}, {row['hi']}]")
else:
    print("QUERY FAILED:", r.status_code)
PYEOF

echo
ok "Done. Full output in $LOG"
echo "Ask Claude to read $LOG and interpret the verdict column."