#!/bin/bash
# ZO-SENTINEL: Master Startup Script

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
echo "=== ZO-SENTINEL Startup ==="

# Start all services
python3 "$SCRIPT_DIR/email_guid_auth.py" &
python3 "$SCRIPT_DIR/advanced_filter_api.py" &
python3 "$SCRIPT_DIR/forensic_detail_api.py" &
python3 "$SCRIPT_DIR/manual_override_api.py" &
python3 "$SCRIPT_DIR/supervisor_auto_updater.py" &

# Start UI server
python3 "$SCRIPT_DIR/ui_server.py" &

echo "All services launched. Use 'bash start_all.sh stop' to halt."
wait
