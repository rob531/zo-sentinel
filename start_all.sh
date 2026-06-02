#!/bin/sh
set -e
set -u

API_PORT=8795
MAX_CHECKS=30
CHECK_INTERVAL=2

SUPERVISORD_PID=""
DAEMONS=""

wait_for_build_watcher() {
    i=0
    while [ $i -lt $MAX_CHECKS ]; do
        if nc -z -w 2 localhost $API_PORT 2>/dev/null; then
            return 0
        fi
        i=$((i + 1))
        sleep $CHECK_INTERVAL
    done
    return 1
}

start_supervisord() {
    if [ ! -f /usr/bin/supervisord ] && [ ! -f /usr/local/bin/supervisord ]; then
        return 1
    fi
    supervisord -c /etc/supervisord.conf 2>&1 &
    SUPERVISORD_PID=$!
    sleep 1
    return 0
}

start_daemons() {
    DAEMONS=$(supervisorctl available 2>/dev/null | awk '{print $1}')
    for daemon in $DAEMONS; do
        supervisorctl start $daemon 2>/dev/null || true
    done
    return 0
}

stop_daemons() {
    if [ -n "$SUPERVISORD_PID" ]; then
        supervisorctl shutdown 2>/dev/null || kill -TERM $SUPERVISORD_PID 2>/dev/null || true
    fi
    return 0
}

cleanup() {
    stop_daemons
    if [ -n "$SUPERVISORD_PID" ]; then
        kill -TERM $SUPERVISORD_PID 2>/dev/null || true
    fi
    exit 0
}

trap cleanup TERM INT

if ! wait_for_build_watcher; then
    exit 1
fi

if ! start_supervisord; then
    exit 2
fi

start_daemons

exit 0