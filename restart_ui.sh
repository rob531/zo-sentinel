#!/usr/bin/env bash
# restart_ui.sh -- Restores the ZO-SENTINEL hosting-tab UI
# Run: bash /home/workspace/zo_sentinel/restart_ui.sh

set -e
echo "=== ZO-SENTINEL UI Recovery ==="

# 1. Restart mesh stack (write_service + InferenceRouter + mesh agents)
echo "[1/3] Restarting mesh stack via zm go..."
zm go
echo "      Waiting 5s for write_service to come up..."
sleep 5

# 2. Kill any stale ui_server process on port 8790
echo "[2/3] Clearing port 8790..."
fuser -k 8790/tcp 2>/dev/null && echo "      Cleared stale process" || echo "      Port was clear"
sleep 1

# 3. Start ui_server.py under nohup (supervisord entry written separately)
echo "[3/3] Starting ui_server.py on port 8790..."
nohup python3 /home/workspace/zo_sentinel/ui_server.py \
  >> /home/workspace/logs/ui_server.log 2>&1 &
UI_PID=$!
echo "      PID: $UI_PID"
sleep 3

# Verify
if curl -s http://127.0.0.1:8790/health | grep -q '"ok"'; then
  echo ""
  echo "[OK] ui_server.py is up: http://127.0.0.1:8790"
  echo "     ZoComputer hosting tab should now preview correctly."
else
  echo "[!!] Health check failed. Check: tail -30 /home/workspace/logs/ui_server.log"
fi

echo ""
echo "Done."