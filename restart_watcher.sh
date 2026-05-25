#!/usr/bin/env bash
# Bounce the build_watcher_api on port 8795
echo "Stopping build_watcher_api (PID 121)..."
kill 121 2>/dev/null; sleep 1
fuser -k 8795/tcp 2>/dev/null; sleep 1
echo "Starting new build_watcher_api..."
nohup python3 /home/workspace/zo_sentinel/build_watcher_api.py \
  >> /home/workspace/logs/build_watcher.log 2>&1 &
echo "PID: $!"
sleep 2
curl -s http://127.0.0.1:8795/health
echo ""