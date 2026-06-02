#!/bin/bash

# Change to project root directory
cd "$(dirname "$0")"

PROJECT_ROOT="$(pwd)"
CONFIG_FILE="$PROJECT_ROOT/supervisord.conf"

# List of required daemon scripts
DAEMONS="mcp_security_d.py write_service.py query_service.py cache_sync_d.py heartbeat_d.py"

# Function to log errors to stderr
log_error() {
    echo "$@" >&2
}

# Check if supervisord.conf exists
if [ ! -f "$CONFIG_FILE" ]; then
    log_error "ERROR: supervisord.conf not found at $CONFIG_FILE"
    exit 1
fi

# Check if all required daemons exist
for daemon in $DAEMONS; do
    if [ ! -f "$PROJECT_ROOT/$daemon" ]; then
        log_error "ERROR: Required daemon '$daemon' not found at $PROJECT_ROOT/$daemon"
        exit 1
    fi
done

# Start supervisord in foreground
exec supervisord -n -c "$CONFIG_FILE"