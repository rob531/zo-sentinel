#!/usr/bin/env bash
# unstick_builder.sh — kills hung builder, restarts clean
# The builder was stuck waiting on Ollama for schema.py which already exists.
# Registry is now pre-seeded with the two valid files.
# On restart it will skip those and move to the real builds.

echo "Killing stuck builder..."
pkill -f 'zo_sentinel_builder.py' 2>/dev/null && echo "  Killed" || echo "  Not running"
sleep 2

echo "Starting builder v1.2.0 via supervisord..."
supervisorctl -c /etc/zo/supervisord-user.conf start zo_sentinel_builder 2>/dev/null \
  || nohup python3 /home/workspace/zo_mesh/zo_sentinel_builder.py \
       >> /home/workspace/logs/zo_sentinel_builder.log 2>&1 &
sleep 2

PID=$(pgrep -f 'zo_sentinel_builder.py' | head -1)
[ -n "$PID" ] && echo "  Builder running PID $PID" || echo "  ERROR: failed to start"
echo "Done."