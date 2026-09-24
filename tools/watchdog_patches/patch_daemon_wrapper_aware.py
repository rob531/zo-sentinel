#!/usr/bin/env python3
"""Idempotent: make watchdog.sh `_daemon()` wrapper-aware and anchor its pgrep.
Defect (observed 2026-09-06, every tick since at least 16:39Z): go.sh launches
registration_drift_check + autopoiesis_bar_tracker under daemon_wrapper.sh
(respawns on SIGTERM), while _daemon() launches a BARE copy on 'down' and
pkills ALL on 'duplicates'. Kill -> wrapper respawns -> bare relaunch -> 2 copies
-> next tick dedup -> forever. Both daemons were SIGTERM'd every 15 min, and
registration_drift_check reported autopoiesis_bar_tracker 'missing' in the gap.
LoopWatch/GraphRefresh: the `bash -c 'while true; do python3 ...'` launcher
cmdline itself matches "python.*<script>", so count was always 2 -> dedup every
tick (the index_graph.py x2 churn in the 09-06 briefing).
Usage: patch_daemon_wrapper_aware.py <watchdog.sh> -> PATCHED|NOCHANGE rc0; rc2 anchor missing."""
import sys
p = sys.argv[1]; s = open(p, encoding="utf-8").read()
MARK = "daemon-wrapper-aware"
if MARK in s:
    print("NOCHANGE"); sys.exit(0)
old = '''_daemon() {
    local script=$1 logfile=$2 name=$3 start_cmd=$4
    local count=$(pgrep -c -f "python.*$script" 2>/dev/null)
    count=${count:-0}
    if [[ "$count" -eq 0 ]]; then
        log "$name down -- restarting"
        eval "nohup $start_cmd >> $LOGS/$logfile 2>&1 &"
        HEALTHY=false; ACTIONS+=("${name}_restart")
        RESTART_VERIFY+=("python.*$script|$name")
    elif [[ "$count" -gt 1 ]]; then
        log "$name duplicates ($count) -- deduplicating"
        pkill -f "python.*$script" 2>/dev/null; sleep 2
        eval "nohup $start_cmd >> $LOGS/$logfile 2>&1 &"
        HEALTHY=false; ACTIONS+=("${name}_dedup")
        RESTART_VERIFY+=("python.*$script|$name")
    fi
}
'''
new = '''# daemon-wrapper-aware (2026-09-06, GH #4722): two fixes, one function.
#  (1) The count pattern is anchored to a python EXECUTABLE ("^python..."), so a
#      `bash -c 'while true; do python3 ...'` launcher no longer counts as a
#      duplicate of its own child (LoopWatch/GraphRefresh were "deduplicated" --
#      i.e. killed and relaunched -- EVERY tick because of this).
#  (2) If go.sh owns the daemon through daemon_wrapper.sh, the wrapper respawns
#      it; this function must never launch a bare copy beside it. Before: kill
#      -> wrapper respawns -> bare relaunch -> 2 copies -> next tick dedup ->
#      forever (registration_drift_check + autopoiesis_bar_tracker SIGTERM'd
#      every 15 min; the drift check filed the bar tracker as "missing" in the
#      gap). Now: strays are killed, the wrapper's own child is kept.
_daemon() {
    local script=$1 logfile=$2 name=$3 start_cmd=$4
    local pat="^python[0-9.]* .*$script"
    local count=$(pgrep -c -f "$pat" 2>/dev/null)
    count=${count:-0}
    local wpid=$(pgrep -f "^bash [^ ]*daemon_wrapper.sh [^ ]* .*$script" 2>/dev/null | head -1)
    if [[ -n "$wpid" ]]; then
        if [[ "$count" -gt 1 ]]; then
            local p
            for p in $(pgrep -f "$pat" 2>/dev/null); do
                [[ "$(ps -o ppid= -p $p 2>/dev/null | tr -d ' ')" == "$wpid" ]] || kill $p 2>/dev/null
            done
            log "$name duplicates ($count) -- killed strays; wrapper $wpid keeps its child"
            HEALTHY=false; ACTIONS+=("${name}_dedup")
        elif [[ "$count" -eq 0 ]]; then
            log "$name down but daemon_wrapper $wpid is alive -- respawn is the wrapper's (backoff <=300s)"
        fi
        return
    fi
    if [[ "$count" -eq 0 ]]; then
        log "$name down -- restarting"
        eval "nohup $start_cmd >> $LOGS/$logfile 2>&1 &"
        HEALTHY=false; ACTIONS+=("${name}_restart")
        RESTART_VERIFY+=("$pat|$name")
    elif [[ "$count" -gt 1 ]]; then
        log "$name duplicates ($count) -- deduplicating"
        pkill -f "$pat" 2>/dev/null; sleep 2
        eval "nohup $start_cmd >> $LOGS/$logfile 2>&1 &"
        HEALTHY=false; ACTIONS+=("${name}_dedup")
        RESTART_VERIFY+=("$pat|$name")
    fi
}
'''
if s.count(old) != 1:
    print("ANCHOR-MISSING"); sys.exit(2)
open(p, "w", encoding="utf-8", newline="\n").write(s.replace(old, new))
print("PATCHED")
