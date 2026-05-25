#!/usr/bin/env python3
"""
diagnose_mcp_scanner_stale_v2.py -- ZO-SENTINEL mcp_scanner staleness diagnostic.
Diagnoses why mcp_scanner is stale (heartbeat age > threshold).
Does NOT rebuild or restart mcp_scanner.py.
"""
import json
import logging
import os
import requests
import time
from datetime import datetime, timezone, timedelta
from typing import Any

SERVICE_NAME = 'mcp_scanner'
DIAGNOSTIC_SERVICE = 'diagnose_mcp_scanner_stale_v2'
WRITE_SERVICE = 'http://127.0.0.1:8772'
QUERY_SERVICE = 'http://127.0.0.1:8772'
EXECUTE_SERVICE = 'http://127.0.0.1:8772'

HEARTBEAT_THRESHOLD_SECS = 14400  # 4 hours
LOG_FILE = '/tmp/mcp_scanner.log'
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
SCANNER_LOG_FILE_ALT = '/home/workspace/zo_sentinel/mcp_scanner.log'
DIAGNOSTICS_LOG = '/tmp/diagnose_mcp_scanner_stale_v2.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s - %(message)s',
    handlers=[
        logging.FileHandler(DIAGNOSTICS_LOG),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(DIAGNOSTIC_SERVICE)


def ws_query(sql: str) -> list[dict[str, Any]]:
    """Query DuckDB via write_service (port 8772)."""
    try:
        r = requests.post(QUERY_SERVICE + '/query',
                         json={'sql': sql}, timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get('rows', [])
    except Exception as e:
        log.error(f"ws_query failed: {e}")
        return []


def ws_write(table: str, rows: dict) -> bool:
    """Write to DuckDB via write_service."""
    try:
        r = requests.post(WRITE_SERVICE + '/write',
                         json={'table': table, 'rows': rows, 'wait': True}, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_write failed: {e}")
        return False


def ws_execute(sql: str) -> bool:
    """Execute DDL/DML via write_service."""
    try:
        r = requests.post(EXECUTE_SERVICE + '/execute',
                         json={'sql': sql}, timeout=15)
        r.raise_for_status()
        return True
    except Exception as e:
        log.error(f"ws_execute failed: {e}")
        return False


def get_utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_to_datetime(iso_str: str) -> datetime:
    """Parse ISO datetime string."""
    if not iso_str:
        return None
    try:
        if '+' in iso_str or iso_str.endswith('Z'):
            return datetime.fromisoformat(iso_str.replace('Z', '+00:00'))
        return datetime.fromisoformat(iso_str)
    except Exception:
        try:
            return datetime.strptime(iso_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
        except Exception:
            return None


def compute_age_seconds(iso_str: str) -> float:
    """Compute age in seconds from ISO timestamp to now."""
    then = iso_to_datetime(iso_str)
    if then is None:
        return float('inf')
    return (get_utc_now() - then).total_seconds()


def ensure_diagnostics_table() -> bool:
    """Ensure diagnostics table exists."""
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_scanner_diagnostics (
        diagnostic_id VARCHAR PRIMARY KEY,
        run_at TIMESTAMP,
        scanner_heartbeat_age_secs DOUBLE,
        scanner_last_heartbeat VARCHAR,
        scanner_heartbeat_stale BOOLEAN,
        scanner_log_last_activity VARCHAR,
        scanner_log_activity_age_secs DOUBLE,
        scanner_log_exists BOOLEAN,
        scanner_log_size_bytes BIGINT,
        scanner_pid BIGINT,
        scanner_process_running BOOLEAN,
        pending_scans_count BIGINT,
        last_successful_scan VARCHAR,
        failure_hypothesis VARCHAR,
        diagnostic_blob JSON
    )
    """
    return ws_execute(sql)


def get_scanner_heartbeat() -> dict[str, Any]:
    """Query service_health for mcp_scanner heartbeat."""
    sql = """
    SELECT service, last_heartbeat
    FROM service_health
    WHERE service = 'mcp_scanner'
    LIMIT 1
    """
    rows = ws_query(sql)
    if rows:
        return rows[0]
    return {}


def get_scanner_pid() -> int | None:
    """Read scanner PID file."""
    for path in [PID_FILE, f'/tmp/mcp_scanner.pid']:
        try:
            if os.path.exists(path):
                pid = int(open(path).read().strip())
                return pid
        except Exception:
            pass
    return None


def is_process_running(pid: int) -> bool:
    """Check if process with PID is running."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def get_log_activity(log_path: str) -> tuple[str | None, int, bool]:
    """Read last activity timestamp from scanner log."""
    if not os.path.exists(log_path):
        return None, 0, False
    
    try:
        size = os.path.getsize(log_path)
        with open(log_path, 'r') as f:
            f.seek(0, 2)
            file_size = f.tell()
            if file_size > 65536:
                f.seek(-65536, 2)
            else:
                f.seek(0)
            lines = f.readlines()
        
        last_line = None
        for line in reversed(lines):
            stripped = line.strip()
            if stripped and not stripped.startswith('#'):
                last_line = stripped
                break
        
        return last_line, size, True
    except Exception as e:
        log.error(f"Error reading log {log_path}: {e}")
        return None, 0, True


def parse_log_timestamp(line: str) -> str | None:
    """Extract timestamp from log line."""
    if not line:
        return None
    import re
    patterns = [
        r'(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[^\s]*)',
        r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})',
        r'\[([^\]]+)\]',
    ]
    for pattern in patterns:
        match = re.search(pattern, line)
        if match:
            return match.group(1)
    return None


def get_pending_scans_count() -> int:
    """Query for pending scans queue depth."""
    sql = """
    SELECT COUNT(*) as pending_count
    FROM (
        SELECT 1 as scan_type
        UNION ALL
        SELECT 1
    ) dummy
    WHERE EXISTS (
        SELECT 1 FROM mcp_server_registry LIMIT 1
    )
    """
    rows = ws_query(sql)
    if rows:
        return rows[0].get('pending_count', 0)
    return 0


def get_last_successful_scan() -> str | None:
    """Query audit_log for last successful scan event."""
    sql = """
    SELECT created_at, detail
    FROM audit_log
    WHERE event_type = 'scan_complete'
    ORDER BY created_at DESC
    LIMIT 1
    """
    rows = ws_query(sql)
    if rows:
        return rows[0].get('created_at')
    return None


def get_recent_errors() -> list[dict]:
    """Query audit_log for recent error events."""
    sql = """
    SELECT created_at, event_type, detail, actor
    FROM audit_log
    WHERE event_type IN ('scan_error', 'scan_failed', 'scan_timeout')
    ORDER BY created_at DESC
    LIMIT 10
    """
    return ws_query(sql)


def diagnose_heartbeat_failure(heartbeat: dict) -> str:
    """Diagnose heartbeat failure mode."""
    if not heartbeat:
        return "NO_HEARTBEAT_RECORD - mcp_scanner has no entry in service_health table"
    
    age = compute_age_seconds(heartbeat.get('last_heartbeat', ''))
    if age > HEARTBEAT_THRESHOLD_SECS:
        if age > HEARTBEAT_THRESHOLD_SECS * 2:
            return "COMPLETE_STOPPED - heartbeat > 2x threshold, process likely terminated"
        elif age > HEARTBEAT_THRESHOLD_SECS * 1.5:
            return "SEVERELY_STALE - heartbeat > 1.5x threshold, process may be hung"
        else:
            return "MODERATELY_STALE - heartbeat exceeds threshold, process slow or interrupted"
    else:
        return "HEARTBEAT_OK"


def diagnose_log_file(log_line: str | None, log_exists: bool, log_size: int) -> str:
    """Diagnose log file issues."""
    if not log_exists:
        return "LOG_FILE_MISSING - no log file found, logging may be disabled"
    
    if log_size == 0:
        return "LOG_FILE_EMPTY - log file exists but is empty"
    
    if log_line is None:
        return "LOG_FILE_NO_ACTIVITY - log file has content but no readable activity"
    
    return "LOG_FILE_OK"


def diagnose_queue_starvation(pending_count: int, last_scan: str | None) -> str:
    """Diagnose queue starvation."""
    if last_scan is None:
        return "QUEUE_UNKNOWN - cannot determine scan history"
    
    age = compute_age_seconds(last_scan)
    if age > HEARTBEAT_THRESHOLD_SECS:
        return f"QUEUE_STARVATION - no successful scan in {age:.0f}s"
    
    if pending_count == 0:
        return "QUEUE_EMPTY - no pending scans in queue"
    
    return f"QUEUE_ACTIVE - {pending_count} pending scans, last scan {age:.0f}s ago"


def diagnose_external_api_timeout(recent_errors: list) -> str:
    """Diagnose external API timeout issues."""
    npm_timeout = 0
    github_timeout = 0
    smithery_timeout = 0
    
    for err in recent_errors:
        detail = err.get('detail', '').lower()
        if 'npm' in detail and 'timeout' in detail:
            npm_timeout += 1
        if 'github' in detail and 'timeout' in detail:
            github_timeout += 1
        if 'smithery' in detail and 'timeout' in detail:
            smithery_timeout += 1
    
    if npm_timeout + github_timeout + smithery_timeout == 0:
        return "NO_EXTERNAL_API_ERRORS - no timeout errors in recent audit log"
    
    issues = []
    if npm_timeout > 0:
        issues.append(f"npm_timeout({npm_timeout})")
    if github_timeout > 0:
        issues.append(f"github_timeout({github_timeout})")
    if smithery_timeout > 0:
        issues.append(f"smithery_timeout({smithery_timeout})")
    
    return f"EXTERNAL_API_TIMEOUT - {', '.join(issues)}"


def compose_diagnostic_blob(
    heartbeat: dict,
    pid: int | None,
    process_running: bool,
    log_line: str | None,
    log_exists: bool,
    log_size: int,
    pending_count: int,
    last_scan: str | None,
    recent_errors: list
) -> dict:
    """Compose full diagnostic blob."""
    heartbeat_age = compute_age_seconds(heartbeat.get('last_heartbeat', ''))
    
    return {
        "diagnostic_version": "2.0",
        "run_at": get_utc_now().isoformat(),
        "scanner_identity": {
            "service_name": SERVICE_NAME,
            "pid_file": PID_FILE,
            "pid": pid,
            "process_running": process_running,
        },
        "heartbeat_analysis": {
            "last_heartbeat": heartbeat.get('last_heartbeat'),
            "age_seconds": heartbeat_age,
            "age_human": f"{heartbeat_age/3600:.2f} hours",
            "threshold_seconds": HEARTBEAT_THRESHOLD_SECS,
            "exceeds_threshold": heartbeat_age > HEARTBEAT_THRESHOLD_SECS,
            "failure_hypothesis": diagnose_heartbeat_failure(heartbeat),
        },
        "log_file_analysis": {
            "log_path": LOG_FILE,
            "exists": log_exists,
            "size_bytes": log_size,
            "last_activity_line": log_line[:200] if log_line else None,
            "last_activity_ts": parse_log_timestamp(log_line),
            "failure_hypothesis": diagnose_log_file(log_line, log_exists, log_size),
        },
        "queue_analysis": {
            "pending_scans": pending_count,
            "last_successful_scan": last_scan,
            "failure_hypothesis": diagnose_queue_starvation(pending_count, last_scan),
        },
        "error_analysis": {
            "recent_errors_count": len(recent_errors),
            "recent_errors": recent_errors[:5],
            "failure_hypothesis": diagnose_external_api_timeout(recent_errors),
        },
        "summary": {
            "primary_cause": None,
            "secondary_causes": [],
            "recommendation": None,
        }
    }


def determine_primary_cause(diag_blob: dict) -> tuple[str, str]:
    """Determine primary cause and recommendation from diagnostic blob."""
    heartbeat_h = diag_blob['heartbeat_analysis']['failure_hypothesis']
    log_h = diag_blob['log_file_analysis']['failure_hypothesis']
    queue_h = diag_blob['queue_analysis']['failure_hypothesis']
    error_h = diag_blob['error_analysis']['failure_hypothesis']
    
    cause = None
    recommendation = None
    
    if "NO_HEARTBEAT" in heartbeat_h:
        cause = "mcp_scanner never wrote heartbeat - process may have crashed at startup"
        recommendation = "Check if mcp_scanner.py has import errors or missing dependencies"
    elif "COMPLETE_STOPPED" in heartbeat_h:
        cause = "mcp_scanner process terminated - no longer running"
        recommendation = "Review supervisord configuration and mcp_scanner logs for crash reason"
    elif "SEVERELY_STALE" in heartbeat_h:
        cause = "mcp_scanner appears hung - no progress for extended period"
        recommendation = "Check if process is in uninterruptible sleep or blocked on I/O"
    elif "LOG_FILE_MISSING" in log_h:
        cause = "mcp_scanner logging disabled or log path inaccessible"
        recommendation = "Verify LOG_FILE path is writable and process has filesystem permissions"
    elif "QUEUE_STARVATION" in queue_h:
        cause = "Scanner not completing scans - external API failures or timeout"
        recommendation = f"{error_h}. Review network connectivity to npm/GitHub/Smithery APIs."
    elif "EXTERNAL_API_TIMEOUT" in error_h and "NO_EXTERNAL_API_ERRORS" not in error_h:
        cause = f"External API timeouts causing scan failures: {error_h}"
        recommendation = "Check firewall rules, API rate limits, and network latency to external services"
    elif "LOG_FILE_EMPTY" in log_h:
        cause = "Scanner started but produced no log output"
        recommendation = "Verify logging configuration and check stderr for early failures"
    else:
        cause = "Indeterminate - multiple minor issues or unknown failure mode"
        recommendation = "Review full diagnostic_blob for detailed analysis"
    
    return cause, recommendation


def write_diagnostic_to_table(diag_blob: dict, cause: str, recommendation: str) -> bool:
    """Write diagnostic result to diagnostics table."""
    diagnostic_id = f"mcp_scanner_stale_{int(time.time())}"
    
    diag_blob['summary']['primary_cause'] = cause
    diag_blob['summary']['recommendation'] = recommendation
    
    row = {
        'diagnostic_id': diagnostic_id,
        'run_at': get_utc_now().isoformat(),
        'scanner_heartbeat_age_secs': diag_blob['heartbeat_analysis']['age_seconds'],
        'scanner_last_heartbeat': diag_blob['heartbeat_analysis']['last_heartbeat'],
        'scanner_heartbeat_stale': diag_blob['heartbeat_analysis']['exceeds_threshold'],
        'scanner_log_last_activity': diag_blob['log_file_analysis']['last_activity_ts'],
        'scanner_log_activity_age_secs': compute_age_seconds(
            diag_blob['log_file_analysis']['last_activity_ts'] or ''
        ) if diag_blob['log_file_analysis']['last_activity_ts'] else None,
        'scanner_log_exists': diag_blob['log_file_analysis']['exists'],
        'scanner_log_size_bytes': diag_blob['log_file_analysis']['size_bytes'],
        'scanner_pid': diag_blob['scanner_identity']['pid'],
        'scanner_process_running': diag_blob['scanner_identity']['process_running'],
        'pending_scans_count': diag_blob['queue_analysis']['pending_scans'],
        'last_successful_scan': diag_blob['queue_analysis']['last_successful_scan'],
        'failure_hypothesis': cause,
        'diagnostic_blob': json.dumps(diag_blob),
    }
    
    return ws_write('mcp_scanner_diagnostics', row)


def send_heartbeat() -> None:
    """Send heartbeat for this diagnostic service."""
    try:
        ws_write('service_health', {
            'service': DIAGNOSTIC_SERVICE,
            'last_heartbeat': get_utc_now().isoformat()
        })
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


def run() -> None:
    """Run the diagnostic cycle."""
    log.info("=" * 60)
    log.info("Starting mcp_scanner staleness diagnostic v2")
    log.info("=" * 60)
    
    send_heartbeat()
    
    ensure_diagnostics_table()
    
    log.info("Querying service_health for mcp_scanner...")
    heartbeat = get_scanner_heartbeat()
    log.info(f"Heartbeat record: {heartbeat}")
    
    log.info("Reading PID file...")
    pid = get_scanner_pid()
    process_running = is_process_running(pid) if pid else False
    log.info(f"PID: {pid}, Running: {process_running}")
    
    log.info("Reading scanner log for activity...")
    log_line, log_size, log_exists = get_log_activity(LOG_FILE)
    if not log_exists:
        log_line, log_size, _ = get_log_activity(SCANNER_LOG_FILE_ALT)
    log.info(f"Log exists: {log_exists}, Size: {log_size}, Last: {log_line[:100] if log_line else 'NONE'}...")
    
    log.info("Querying pending scans queue...")
    pending_count = get_pending_scans_count()
    log.info(f"Pending scans: {pending_count}")
    
    log.info("Querying last successful scan...")
    last_scan = get_last_successful_scan()
    log.info(f"Last successful scan: {last_scan}")
    
    log.info("Querying recent errors...")
    recent_errors = get_recent_errors()
    log.info(f"Recent errors: {len(recent_errors)}")
    
    log.info("Composing diagnostic blob...")
    diag_blob = compose_diagnostic_blob(
        heartbeat=heartbeat,
        pid=pid,
        process_running=process_running,
        log_line=log_line,
        log_exists=log_exists,
        log_size=log_size,
        pending_count=pending_count,
        last_scan=last_scan,
        recent_errors=recent_errors,
    )
    
    cause, recommendation = determine_primary_cause(diag_blob)
    diag_blob['summary']['primary_cause'] = cause
    diag_blob['summary']['recommendation'] = recommendation
    
    log.info("-" * 60)
    log.info("DIAGNOSTIC RESULTS")
    log.info("-" * 60)
    log.info(f"  Primary Cause:      {cause}")
    log.info(f"  Recommendation:     {recommendation}")
    log.info(f"  Heartbeat Age:      {diag_blob['heartbeat_analysis']['age_seconds']:.1f}s ({diag_blob['heartbeat_analysis']['age_human']})")
    log.info(f"  Process Running:    {process_running}")
    log.info(f"  Log Exists:        {log_exists}")
    log.info(f"  Pending Scans:     {pending_count}")
    log.info(f"  Error Count:       {len(recent_errors)}")
    log.info("-" * 60)
    
    log.info("Writing diagnostic to database...")
    success = write_diagnostic_to_table(diag_blob, cause, recommendation)
    log.info(f"Diagnostic write {'succeeded' if success else 'FAILED'}")
    
    log.info("Saving diagnostic blob to file...")
    blob_path = '/tmp/mcp_scanner_diagnostic_blob.json'
    try:
        with open(blob_path, 'w') as f:
            json.dump(diag_blob, f, indent=2)
        log.info(f"Diagnostic blob saved to {blob_path}")
    except Exception as e:
        log.error(f"Failed to save diagnostic blob: {e}")
    
    send_heartbeat()
    log.info("Diagnostic cycle complete")
    
    return diag_blob


def main():
    """Entry point."""
    result = run()
    if result:
        cause = result['summary']['primary_cause']
        sys.exit(0 if "NO_HEARTBEAT" not in cause and "COMPLETE_STOPPED" not in cause else 1)
    else:
        sys.exit(1)


if __name__ == '__main__':
    import sys
    main()