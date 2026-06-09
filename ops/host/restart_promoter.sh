#!/usr/bin/env bash
# restart_promoter.sh -- proposed_to_pending_promoter died ~02:59 (NOT in the
# watchdog's coverage, so it stayed dead). 20h of generated directives sit in
# proposed/ unpromoted -> goose has no eligible work -> 0 builds. Restart it
# (pgrep-guarded) to drain proposed/ -> pending/; goose_runner picks the new
# directives up on its next ~60s cycle and starts building.
#
# (Durable fix shipped in watchdog v3.7: the promoter is now watchdog-covered.)
#
# Run ONE command:  bash /home/workspace/zo_sentinel/restart_promoter.sh
set -uo pipefail
SENT=/home/workspace/zo_sentinel; LOGS=/home/workspace/logs
cd "$SENT" || { echo "FATAL: cannot cd $SENT"; exit 1; }
PAT='[p]romoters.proposed_to_pending_promoter'
if pgrep -f "$PAT" >/dev/null; then
  echo "promoter already running (pid $(pgrep -f "$PAT" | head -1)) -- not restarting"
else
  nohup python3 -m zo_sentinel.promoters.proposed_to_pending_promoter >> "$LOGS/proposed_to_pending_promoter.log" 2>&1 &
  sleep 3
  echo "promoter started: pid $(pgrep -f "$PAT" | head -1 || echo NOT-RUNNING)"
fi
sleep 10
echo "--- promoter log tail (look for 'promoted=N' with N>0) ---"
tail -10 "$LOGS/proposed_to_pending_promoter.log"
echo
echo "--- pending directive count (newly promoted work for goose) ---"
ls /home/workspace/zo_sentinel/directives/pending/*.json 2>/dev/null | wc -l
echo "=== done -- watch goose_runner.log; eligible directives build within ~1-2 cycles ==="
