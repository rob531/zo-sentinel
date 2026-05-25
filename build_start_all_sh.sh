#!/bin/bash
set -e

PROJECT_DIR="/home/workspace/zo_sentinel"
LOG_DIR="/home/workspace/logs"
RUN_DIR="/var/run/sentinel"
SUPERVISOR_CONF="${PROJECT_DIR}/supervisord_sentinel_full.conf"
MAX_WAIT=30

mkdir -p "$LOG_DIR" "$RUN_DIR"

build_daemon() {
    local name="$1"
    local command="$2"
    echo "[BUILD] Compiling $name..."
    eval "$command" > "${LOG_DIR}/${name}.build.log" 2>&1
}

start_daemon() {
    local name="$1"
    local command="$2"
    local pidfile="${RUN_DIR}/${name}.pid"
    
    echo "[START] Launching $name..."
    setsid bash -c "$command" > "${LOG_DIR}/${name}.log" 2>&1 &
    local pid=$!
    echo $pid > "$pidfile"
    
    local waited=0
    while [ $waited -lt $MAX_WAIT ]; do
        if [ -d "/proc/$pid" ] && grep -q "ready\|listening\|started\|running" "${LOG_DIR}/${name}.log" 2>/dev/null || [ $waited -gt 5 ]; then
            echo "[OK] $name started (PID=$pid)"
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done
    
    if [ -d "/proc/$pid" ]; then
        echo "[OK] $name started (PID=$pid)"
        return 0
    fi
    
    echo "[FAIL] $name failed to start within ${MAX_WAIT}s"
    return 1
}

echo "=== ZO-SENTINEL Build & Start All ==="

if [ ! -f "$SUPERVISOR_CONF" ]; then
    echo "[ERROR] Config not found: $SUPERVISOR_CONF"
    exit 1
fi

declare -A commands
declare -a order
current_program=""

while IFS= read -r line || [ -n "$line" ]; do
    line=$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')
    
    if [[ "$line" =~ ^\[program:([^]]+)\] ]]; then
        if [ -n "$current_program" ]; then
            order+=("$current_program")
        fi
        current_program="${BASH_REMATCH[1]}"
        commands[$current_program]=""
    elif [ -n "$current_program" ] && [[ "$line" =~ ^command= ]]; then
        commands[$current_program]="${line#command=}"
    fi
done < "$SUPERVISOR_CONF"

if [ -n "$current_program" ]; then
    order+=("$current_program")
fi

failed=0
for prog in "${order[@]}"; do
    cmd="${commands[$prog]}"
    if [ -z "$cmd" ]; then
        echo "[WARN] No command for $prog, skipping"
        continue
    fi
    
    build_daemon "$prog" "$cmd" || {
        echo "[FAIL] Build failed for $prog"
        failed=1
        continue
    }
    
    start_daemon "$prog" "$cmd" || {
        failed=1
    }
done

if [ $failed -eq 1 ]; then
    echo "=== SOME DAEMONS FAILED ==="
    exit 1
fi

echo "=== ALL DAEMONS STARTED SUCCESSFULLY ==="
exit 0