#!/usr/bin/env bash
# run_publisher_daemon.sh -- cleanly (re)launch the PR publisher daemon with the
# CWD fix + clone dir. Exists because the long multi-line launch one-liner keeps
# mangling on terminal paste (python3 -m gets split from the module, env runs
# with no command, etc.). Living in a file, it can't be mangled.
#
# Usage (on the host):
#   bash /home/workspace/zo_sentinel/tools/run_publisher_daemon.sh
#   PR_PUBLISHER_CLONE_DIR=/some/other/clone bash .../run_publisher_daemon.sh
#
# Dormant-safe: the publisher still no-ops until .pr_publisher_enabled exists.
set -u

SENTINEL=/home/workspace/zo_sentinel
CLONE="${PR_PUBLISHER_CLONE_DIR:-/home/workspace/zo_sentinel_pub_clone}"
# Daily PR cap. The legacy default (8) existed ONLY to stay under the free
# 2000 Actions-min/mo on a PRIVATE repo (see publisher.py DEFAULT_DAILY_CAP).
# The repo is now PUBLIC -> Actions minutes are free, so that rationale is gone.
# Raised to 20 (staged; council 2026-06-16) to let the post-#187 novel build flow
# publish without flooding main. Override with PR_PUBLISHER_DAILY_CAP=<n>.
CAP="${PR_PUBLISHER_DAILY_CAP:-20}"
LOG=/home/workspace/logs/pr_publisher.log

echo "stopping any existing publisher loop..."
pkill -f 'zo_sentinel.publisher run-once' 2>/dev/null || true
sleep 1

if [ ! -d "$CLONE/.git" ]; then
    echo "WARNING: clone dir '$CLONE' is not a git checkout -- with no real clone"
    echo "the publisher falls back to FakeGitOps (no real PRs). Clone first:"
    echo "  git clone https://github.com/rob531/zo-sentinel $CLONE"
fi

echo "launching publisher (PYTHONPATH=$SENTINEL, clone=$CLONE, daily_cap=$CAP)..."
nohup env PYTHONPATH="$SENTINEL" PR_PUBLISHER_CLONE_DIR="$CLONE" PR_PUBLISHER_DAILY_CAP="$CAP" bash -c \
    "cd $SENTINEL && while true; do python3 -m zo_sentinel.publisher run-once; sleep 600; done" \
    >> "$LOG" 2>&1 &

sleep 4
echo "--- running publisher processes ---"
pgrep -af 'zo_sentinel.publisher run-once' || echo "  (none -- launch failed, see $LOG)"
echo "--- tail $LOG ---"
tail -8 "$LOG" 2>/dev/null || echo "  (no log yet)"
echo
echo "Done. The publisher loops every 600s; dormant until .pr_publisher_enabled."
