#!/usr/bin/env bash
# refresh_code.sh -- pull latest main onto the box. Deploys today's merged fixes,
# incl. the publisher absolute-path stall fix (#115). The publisher runs a fresh
# `python3 -m zo_sentinel.publisher run-once` each cycle, so it picks up the fix
# automatically and drains the stalled backlog within ~10min -- NO daemon restart.
#
# Run ONE command:  bash /home/workspace/zo_sentinel/refresh_code.sh
set -uo pipefail
cd /home/workspace/zo_sentinel || { echo "FATAL: cannot cd"; exit 1; }
git fetch origin main -q && git reset --hard origin/main -q
echo "HEAD: $(git rev-parse --short HEAD)  $(git log -1 --pretty=%s | cut -c1-54)"
python3 -c "import ast; ast.parse(open('zo_sentinel/publisher/gitops.py').read()); print('publisher syntax OK')"
echo "publisher loop alive: $(pgrep -fc 'publisher run-once' 2>/dev/null || echo 0) process(es)"
echo "Done -- auto/build PRs should resume within ~10min as the poison artifact is quarantined and the backlog drains."
