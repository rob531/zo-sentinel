#!/usr/bin/env python3
"""
mcp_scanner_heartbeat_recovery.py -- Diagnose stale mcp_scanner heartbeat.
Threshold: 14400s (4h). Stale age: 5h19m (~19020s).
"""
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone

import requests

SERVICE_NAME = 'mcp_scanner_heartbeat_recovery'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
QUERY_URL = WRITE_SERVICE_URL + '/query'
WRITE_URL = WRITE_SERVICE_URL + '/write'
LOG_FILE = '/home/workspace/logs/mcp_scanner_heartbeat_recovery.log'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
HEARTBEAT_STALE_THRESHOLD = 14400  # 4 hours in seconds

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()]
)
log = logging.getLogger(__name__)


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql):
    """Query DuckDB via write_service."""
    try:
        r = requests.post(QUERY_URL, json={'sql': sql}, timeout=10)
        if r.status_code == 200:
            return r.json().get('rows', [])
        log.error("ws_query failed: %s", r.text)
        return []
    except Exception as e:
        log.error("ws_query exception: %s", e)
        return []


def ws_write(table, rows):
    """Write to DuckDB via write_service."""
    try:
        payload = {'table': table, 'rows': rows, 'wait': True}
        r = requests.post(WRITE_URL, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error("ws_write exception: %s", e)
        return False


def check_single_instance():
    """Guard against multiple instances."""
    if os.path.exists(PID_FILE):
        try:
            old_pid = int(open(PID_FILE).read().strip())
            os.kill(old_pid, 0)
            log.warning('Already running with PID %d', old_pid)
            sys.exit(1)
        except (OSError, ValueError):
            pass
    open(PID_FILE, 'w').write(str(os.getpid()))


def remove_pid_file():
    try:
        os.remove(PID_FILE)
    except Exception:
        pass


def signal_handler(signum, frame):
    log.info('Received signal %d, shutting down', signum)
    remove_pid_file()
    sys.exit(0)


def get_heartbeat_from_db():
    """Query service_health table for mcp_scanner last_heartbeat."""
    sql = """
    SELECT service, last_heartbeat 
    FROM service_health 
    WHERE service = 'mcp_scanner'
    """
    rows = ws_query(sql)
    if rows:
        return rows[0].get('last_heartbeat')
    return None


def calculate_age_seconds(last_heartbeat_str):
    """Calculate seconds since last_heartbeat."""
    if not last_heartbeat_str:
        return None
    try:
        # Parse ISO 8601 timestamp
        if last_heartbeat_str.endswith('Z'):
            dt = datetime.fromisoformat(last_heartbeat_str[:-1]).replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(last_heartbeat_str).replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - dt).total_seconds()
    except Exception as e:
        log.error("Failed to parse timestamp '%s': %s", last_heartbeat_str, e)
        return None


def check_process_running():
    """Check if mcp_scanner process is alive via ps."""
    try:
        result = subprocess.run(
            ['ps', 'aux'],
            capture_output=True,
            text=True,
            timeout=5
        )
        for line in result.stdout.split('\n'):
            if 'mcp_scanner.py' in line and 'grep' not in line:
                # Found process, return PID and full line
                parts = line.split()
                if len(parts) > 1:
                    pid = parts[1]
                    return {'alive': True, 'pid': pid, 'line': line.strip()}
        return {'alive': False, 'pid': None, 'line': None}
    except Exception as e:
        log.error("ps command failed: %s", e)
        return {'alive': False, 'error': str(e)}


def check_log_errors():
    """Inspect mcp_scanner log for recent errors."""
    log_path = '/home/workspace/logs/mcp_scanner.log'
    errors = []
    if os.path.exists(log_path):
        try:
            # Read last 100 lines
            result = subprocess.run(
                ['tail', '-100', log_path],
                capture_output=True,
                text=True,
                timeout=5
            )
            for line in result.stdout.split('\n'):
                if 'ERROR' in line or 'Exception' in line or 'Failed' in line:
                    errors.append(line)
        except Exception as e:
            log.error("Failed to read log: %s", e)
    else:
        log.warning("Log file not found: %s", log_path)
    return errors


def check_write_service_connectivity():
    """Verify write_service is responsive."""
    try:
        r = requests.post(WRITE_SERVICE_URL + '/query',
                         json={'sql': 'SELECT 1'},
                         timeout=5)
        return {'reachable': True, 'status': r.status_code}
    except Exception as e:
        return {'reachable': False, 'error': str(e)}


def check_daemon_file_exists():
    """Check if mcp_scanner.py exists and is valid."""
    path = '/home/workspace/zo_sentinel/mcp_scanner.py'
    exists = os.path.exists(path)
    if exists:
        try:
            size = os.path.getsize(path)
            return {'exists': True, 'path': path, 'size': size}
        except Exception:
            pass
    return {'exists': False, 'path': path}


def build_diagnostic_report(heartbeat_age, process_info, log_errors,
                            ws_health, daemon_info):
    """Build diagnostic report with restart recommendation."""
    report = []
    report.append("=" * 60)
    report.append("MCP_SCANNER HEARTBEAT STALENESS DIAGNOSTIC REPORT")
    report.append("Generated: " + utc_now_iso())
    report.append("=" * 60)
    report.append("")

    # Heartbeat status
    report.append("1. HEARTBEAT STATUS")
    report.append("-" * 40)
    if heartbeat_age:
        age_hours = heartbeat_age / 3600
        report.append(f"   Last heartbeat: {heartbeat_age:.0f}s ago ({age_hours:.1f}h)")
        report.append(f"   Stale threshold: {HEARTBEAT_STALE_THRESHOLD}s (4h)")
        if heartbeat_age > HEARTBEAT_STALE_THRESHOLD:
            report.append(f"   Status: STALE (exceeds threshold by {heartbeat_age - HEARTBEAT_STALE_THRESHOLD:.0f}s)")
        else:
            report.append("   Status: OK (within threshold)")
    else:
        report.append("   Status: NO HEARTBEAT RECORD FOUND")
    report.append("")

    # Process status
    report.append("2. PROCESS STATUS (ps aux)")
    report.append("-" * 40)
    if process_info.get('alive'):
        report.append(f"   Status: RUNNING")
        report.append(f"   PID: {process_info.get('pid')}")
        report.append(f"   Command: {process_info.get('line', 'N/A')}")
    else:
        report.append("   Status: NOT RUNNING (dead)")
    report.append("")

    # Log errors
    report.append("3. RECENT LOG ERRORS (last 100 lines)")
    report.append("-" * 40)
    if log_errors:
        for err in log_errors[:10]:
            report.append(f"   {err}")
    else:
        report.append("   No errors found in recent logs")
    report.append("")

    # WriteService connectivity
    report.append("4. WRITE_SERVICE CONNECTIVITY")
    report.append("-" * 40)
    if ws_health.get('reachable'):
        report.append(f"   Status: REACHABLE")
        report.append(f"   HTTP status: {ws_health.get('status')}")
    else:
        report.append(f"   Status: UNREACHABLE")
        report.append(f"   Error: {ws_health.get('error', 'Unknown')}")
    report.append("")

    # Daemon file
    report.append("5. DAEMON FILE STATUS")
    report.append("-" * 40)
    if daemon_info.get('exists'):
        report.append(f"   File: {daemon_info.get('path')}")
        report.append(f"   Size: {daemon_info.get('size', 'N/A')} bytes")
        report.append("   Status: INTACT")
    else:
        report.append(f"   File: {daemon_info.get('path')}")
        report.append("   Status: MISSING")
    report.append("")

    # Diagnosis and recommendation
    report.append("6. DIAGNOSIS")
    report.append("-" * 40)

    is_dead = not process_info.get('alive', False)
    is_stale = heartbeat_age and heartbeat_age > HEARTBEAT_STALE_THRESHOLD
    file_ok = daemon_info.get('exists', False)
    ws_ok = ws_health.get('reachable', False)

    if is_dead and file_ok and ws_ok:
        report.append("   Cause: Daemon process is DEAD (not running)")
        report.append("   Heartbeat stopped because process died")
        report.append("")
        report.append("7. RESTART RECOMMENDATION")
        report.append("-" * 40)
        report.append("   Daemon file is intact. Manual restart required:")
        report.append("")
        report.append("   Option A - Supervisor (if managed by supervisord):")
        report.append("     sudo supervisorctl restart mcp_scanner")
        report.append("")
        report.append("   Option B - Direct launch:")
        report.append("     cd /home/workspace/zo_sentinel && python3 mcp_scanner.py &")
        report.append("")
        report.append("   Option C - nohup:")
        report.append("     nohup python3 /home/workspace/zo_sentinel/mcp_scanner.py > /home/workspace/logs/mcp_scanner.out 2>&1 &")
    elif not ws_ok:
        report.append("   Cause: WRITE_SERVICE is UNREACHABLE")
        report.append("   Heartbeat fails because DB writes are blocked")
        report.append("   Check write_service process on port 8772")
    elif not is_stale and process_info.get('alive'):
        report.append("   Cause: UNKNOWN - Process running but heartbeat stale")
        report.append("   Possible: heartbeat loop error, network issue")
        report.append("   Check log errors section above")
    else:
        report.append("   Cause: Unable to determine")
        report.append("   Manual investigation required")

    report.append("")
    report.append("=" * 60)

    return "\n".join(report)


def main():
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    log.info("Starting mcp_scanner heartbeat diagnostic")

    # Step 1: Get heartbeat from DB
    log.info("Step 1: Querying service_health for mcp_scanner")
    last_heartbeat = get_heartbeat_from_db()
    heartbeat_age = calculate_age_seconds(last_heartbeat)
    log.info("Last heartbeat: %s (age: %s)", last_heartbeat, heartbeat_age)

    # Step 2: Check process
    log.info("Step 2: Checking if mcp_scanner process is running")
    process_info = check_process_running()
    log.info("Process alive: %s", process_info.get('alive'))

    # Step 3: Check logs
    log.info("Step 3: Inspecting mcp_scanner log for errors")
    log_errors = check_log_errors()
    log.info("Found %d errors in recent logs", len(log_errors))

    # Step 4: WriteService connectivity
    log.info("Step 4: Verifying write_service connectivity")
    ws_health = check_write_service_connectivity()
    log.info("WriteService reachable: %s", ws_health.get('reachable'))

    # Step 5: Daemon file
    log.info("Step 5: Checking if mcp_scanner.py exists")
    daemon_info = check_daemon_file_exists()
    log.info("Daemon file exists: %s", daemon_info.get('exists'))

    # Build report
    report = build_diagnostic_report(
        heartbeat_age, process_info, log_errors,
        ws_health, daemon_info
    )

    print("\n" + report)

    # Write diagnostic to log
    log.info("Diagnostic complete:\n%s", report)

    # Write diagnostic record to service_health
    ws_write('service_health', {
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'meta': f'stale_mcp_scanner={heartbeat_age}s, process_alive={process_info.get("alive")}'
    })

    remove_pid_file()
    log.info("Diagnostic completed successfully")
    sys.exit(0)


if __name__ == '__main__':
    main()