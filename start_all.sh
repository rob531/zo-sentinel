#!/bin/sh
# start_all.sh - ZO-SENTINEL daemon orchestration via supervisord
# strict POSIX sh, no bashisms

set -u

# Configuration defaults (may be overridden via environment)
: "${CHECK_INTERVAL:=2}"
: "${MAX_CHECKS:=30}"
: "${SHUTDOWN_TIMEOUT:=30}"
: "${SUPERVISOR_CONF:-}"

SKIP_HEALTH_CHECK=0
SHUTDOWN=0
STARTUP_ORDER=""
DAEMON_RESULTS=""

# Daemon definitions: name:port:dependencies
DAEMONS="
zo_sentinel_core:8791:
build_watcher_api:8795:build_watcher_api
metrics_exporter:8796:build_watcher_api
dashboard_api:8797:build_watcher_api
alert_service:8798:build_watcher_api,zo_sentinel_core
"

# Output functions
timestamp() {
    date '+%Y-%m-%d %H:%M:%S'
}

log_info() {
    echo "[$(timestamp)] INFO: $*"
}

log_warn() {
    echo "[$(timestamp)] WARN: $*" >&2
}

log_error() {
    echo "[$(timestamp)] ERROR: $*" >&2
}

# Usage information
usage() {
    cat <<'EOF'
Usage: start_all.sh [OPTIONS]

Options:
    --skip-health-check    Skip the initial build_watcher_api health check
    --help                 Show this help message and exit

Environment variables:
    CHECK_INTERVAL         Seconds between health checks (default: 2)
    MAX_CHECKS             Maximum health check attempts (default: 30)
    SHUTDOWN_TIMEOUT       Seconds to wait during shutdown (default: 30)
    SUPERVISOR_CONF        Path to supervisord configuration file

Exit codes:
    0   Success
    1   Error (general)
    2   Dependency timeout (health check failed)

EOF
}

# Validate prerequisites
prerequisites_ok() {
    _missing=""
    if ! command -v supervisorctl >/dev/null 2>&1; then
        _missing="supervisorctl"
    fi
    if ! command -v awk >/dev/null 2>&1; then
        _missing="$_missing awk"
    fi
    if ! command -v head >/dev/null 2>&1; then
        _missing="$_missing head"
    fi
    if ! command -v sleep >/dev/null 2>&1; then
        _missing="$_missing sleep"
    fi
    if ! command -v grep >/dev/null 2>&1; then
        _missing="$_missing grep"
    fi
    if [ -n "$_missing" ]; then
        log_error "Missing required commands: $_missing"
        return 1
    fi
    if [ -z "$SUPERVISOR_CONF" ]; then
        log_error "SUPERVISOR_CONF is not set"
        return 1
    fi
    if [ ! -f "$SUPERVISOR_CONF" ]; then
        log_error "Supervisor config not found: $SUPERVISOR_CONF"
        return 1
    fi
    return 0
}

# Get daemon field by index
get_daemon_field() {
    printf '%s\n' "$DAEMONS" | awk -F: -v name="$1" -v idx="$2" '
        $1 == name {print $idx}
    '
}

# Recursively compute dependency order (topological sort)
get_dependency_order() {
    _name="$1"
    _visited="$2"
    _result="$3"

    if printf '%s' "$_visited" | grep -qE "(^|[^a-z_])(${_name})([^a-z_]|$)" 2>/dev/null; then
        return 0
    fi
    _visited="$_visited $_name"

    _deps_raw="$(get_daemon_field "$_name" 3)"
    if [ -n "$_deps_raw" ]; then
        _dep_line="$_deps_raw"
        _deps="$(printf '%s' "$_dep_line" | tr ',' '\n')"
        for _dep in $_deps; do
            case "$_dep" in
                ''|$_name) continue ;;
            esac
            _result="$(get_dependency_order "$_dep" "$_visited" "$_result")"
            _visited="$(printf '%s' "$_result" | awk '{print; exit}' ORS=" ")";;
        esac
    fi

    case " $_result " in
        *" $_name "*) ;;
        *) _result="$_result $_name" ;;
    esac
    printf '%s\n' "$_result"
}

# Compute full startup order
compute_startup_order() {
    _order=""
    for _name in $(printf '%s\n' "$DAEMONS" | awk -F: 'NF >= 1 && $1 != "" {print $1}'); do
        _order="$(get_dependency_order "$_name" "" "$_order")"
    done
    printf '%s\n' "$_order"
}

# Health check using curl or netcat
check_health_endpoint() {
    _port="$1"
    _count=0

    while [ "$_count" -lt "$MAX_CHECKS" ]; do
        _count=$((_count + 1))

        if command -v curl >/dev/null 2>&1; then
            if curl -sf "http://127.0.0.1:$_port/health" >/dev/null 2>&1; then
                return 0
            fi
        elif command -v nc >/dev/null 2>&1; then
            if nc -z -w 1 127.0.0.1 "$_port" 2>/dev/null; then
                return 0
            fi
        else
            _status="$(supervisorctl -c "$SUPERVISOR_CONF" status 2>/dev/null | \
                grep -E "^[a-z_]+[[:space:]]+RUNNING" | head -n1)"
            if [ -n "$_status" ]; then
                return 0
            fi
        fi

        if [ "$_count" -lt "$MAX_CHECKS" ]; then
            sleep "$CHECK_INTERVAL" 2>/dev/null || sleep 1
        fi
    done
    return 1
}

# Check if daemon is in RUNNING state
daemon_is_running() {
    _name="$1"
    _status="$(supervisorctl -c "$SUPERVISOR_CONF" status "$_name" 2>/dev/null | head -n1)"
    case "$_status" in
        "${_name}"[[:space:]]*RUNNING*) return 0 ;;
    esac
    return 1
}

# Check if all core daemons are running
check_all_running() {
    for _name in $(printf '%s\n' "$DAEMONS" | awk -F: 'NF >= 1 && $1 != "" {print $1}'); do
        if ! daemon_is_running "$_name"; then
            return 1
        fi
    done
    return 0
}

# Start all daemons in dependency order
start_all_daemons() {
    _result=""
    _failed=""
    _started=""

    log_info "Computing startup order..."
    STARTUP_ORDER="$(compute_startup_order)" || return 1
    log_info "Startup order:$STARTUP_ORDER"

    if [ $SHUTDOWN -eq 1 ]; then
        log_info "Shutdown requested, skipping startup"
        return 1
    fi

    for _name in $STARTUP_ORDER; do
        _entry_found=""
        for _line in $DAEMONS; do
            _entry_name="$(printf '%s\n' "$_line" | awk -F: '{print $1}')"
            if [ "$_entry_name" = "$_name" ]; then
                _entry_found="$_line"
                break
            fi
        done

        if [ -z "$_entry_found" ]; then
            continue
        fi

        _port="$(printf '%s\n' "$_entry_found" | awk -F: '{print $2}')"

        log_info "Checking daemon: $_name (port $_port)"

        if daemon_is_running "$_name"; then
            log