#!/usr/bin/env bash
# restart_promoter.sh -- proposed_to_pending_promoter died ~02:59 (NOT in the
# watchdog's coverage, so it stayed dead). 20h of generated directives sit in
# proposed/ unpromoted -> goose has no eligible work -> 0 builds. Restart it
# (pgrep-guarded) to drain proposed/ -> pending/; goose_runner picks the new
# directives up on its next ~60s cycle and starts building.
#
# (Durable fix shipped in watchdog v3.7: the promoter is now watchdog-covered.)
#
# 2026-08-22 (GH #3415 prevention 1, FU-349): this script used to print a log
# tail and "=== done ===" in EVERY path -- including "already running, did
# nothing" and "launched a process that died at import 3s later". On 08-22 it
# reported a restart it had not performed (pid unchanged), which cost the first
# arming of the marker-guard fix. It now verifies the OUTCOME: the process must
# be alive 10s after launch, and in --restart mode the pid must have CHANGED.
# Any other result is a loud nonzero FAILURE, never a "done".
#
# Run:  bash restart_promoter.sh            # start only if dead
#       bash restart_promoter.sh --restart  # cycle a RUNNING promoter onto
#                                           # current on-disk code (kills old)
set -uo pipefail
SENT=/home/workspace/zo_sentinel; LOGS=/home/workspace/logs
cd "$SENT" || { echo "FATAL: cannot cd $SENT"; exit 1; }
PAT='[p]romoters.proposed_to_pending_promoter'
MODE="${1:-}"

pre_pid=$(pgrep -f "$PAT" | head -1 || true)

if [[ -n "$pre_pid" && "$MODE" != "--restart" ]]; then
  echo "promoter already running (pid $pre_pid) -- NO ACTION TAKEN."
  echo "To cycle it onto current on-disk code: bash $0 --restart"
  exit 0
fi

if [[ -n "$pre_pid" ]]; then
  echo "cycling promoter: killing pid $pre_pid"
  pkill -f "$PAT" 2>/dev/null || true
  sleep 2
fi

nohup bash -c "cd $SENT && exec python3 -m zo_sentinel.promoters.proposed_to_pending_promoter" \
    >> "$LOGS/proposed_to_pending_promoter.log" 2>&1 &

# GH #3415 prevention 1: a restart that yields no surviving process is a
# FAILURE, not a recovery. The import-crash class dies in <3s; 10s is enough
# to observe it. Verify the outcome before claiming anything.
sleep 10

new_pid=$(pgrep -f "$PAT" | head -1 || true)
if [[ -z "$new_pid" ]]; then
  echo "RESTART FAILED: promoter is NOT alive 10s after launch."
  echo "--- last log lines (look for an import traceback) ---"
  tail -15 "$LOGS/proposed_to_pending_promoter.log"
  exit 1
fi
if [[ -n "$pre_pid" && "$new_pid" == "$pre_pid" ]]; then
  echo "RESTART FAILED: pid unchanged ($pre_pid) -- the old process survived; no restart happened."
  exit 1
fi

echo "promoter alive: pid $new_pid (verified 10s after launch)"
echo "--- promoter log tail (look for 'promoted=N' with N>0) ---"
tail -10 "$LOGS/proposed_to_pending_promoter.log"
echo
echo "--- pending directive count (newly promoted work for goose) ---"
ls "$SENT"/directives/pending/*.json 2>/dev/null | wc -l
echo "=== done: restart VERIFIED (alive at +10s; pid changed where applicable) ==="
