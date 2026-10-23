import requests
import os
import signal
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

SERVICE_NAME = 'assessment_auditor'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772'
EXECUTE_URL = 'http://127.0.0.1:8772'
HEARTBEAT_INTERVAL = 60
AUDIT_INTERVAL = 86400
PID_FILE = '/tmp/assessment_auditor.pid'
AUDIT_REPORT_PATH = '/home/workspace/zo_sentinel/AUDIT_REPORT.md'

import logging
LOG_DIR = Path('/home/workspace/logs')
LOG_DIR.mkdir(parents=True, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    filename=str(LOG_DIR / f'{SERVICE_NAME}.log')
)
log = logging.getLogger(__name__)


def ws_query(sql, params=None):
    """Execute SQL query against DuckDB via write_service."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(f'{QUERY_SERVICE_URL}/query', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_write(table, rows, wait=True):
    """Write rows to DuckDB table via write_service."""
    if isinstance(rows, dict):
        rows = [rows]
    payload = {'table': table, 'rows': rows, 'wait': wait}
    resp = requests.post(f'{WRITE_SERVICE_URL}/write', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql, params=None):
    """Execute DDL/DML via write_service execute endpoint."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(f'{EXECUTE_URL}/execute', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def send_heartbeat():
    """Send service heartbeat to service_health table."""
    try:
        ws_write('service_health', {
            'service': SERVICE_NAME,
            'last_heartbeat': datetime.now(timezone.utc).isoformat(),
            'status': 'running'
        })
    except Exception as e:
        log.error(f"Heartbeat failed: {e}")


def check_single_instance():
    """Ensure only one instance of daemon runs."""
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Another instance already running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            log.info(f"Stale PID file found, removing")
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file():
    """Remove the PID file on exit."""
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    log.info(f"Received signal {signum}, shutting down")
    remove_pid_file()
    sys.exit(0)


def utc_now_iso():
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def check_low_confidence_verdicts(threshold=50, lookback_days=7):
    """Find recent verdicts with low confidence scores that may need review."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    sql = """
        SELECT 
            r.server_id,
            r.name,
            r.verdict,
            r.trust_score,
            r.scan_count,
            r.registry_source
        FROM mcp_server_registry r
        WHERE r.verdict IN ('UNKNOWN', 'AMBER_UNVERIFIED', 'CAUTION_LIMITED')
          AND r.trust_score < ?
          AND r.scan_count < 3
    """
    result = ws_query(sql, params=[threshold])
    return result.get('rows', [])


def check_unscored_servers(lookback_days=7):
    """Find servers that have never been scored."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).isoformat()
    sql = """
        SELECT 
            r.server_id,
            r.name,
            r.verdict,
            r.registry_source,
            r.scan_count
        FROM mcp_server_registry r
        LEFT JOIN mcp_signal_scores s ON r.server_id = s.server_id
        WHERE s.server_id IS NULL
        ORDER BY r.scan_count ASC, r.registry_source
        LIMIT 100
    """
    result = ws_query(sql)
    return result.get('rows', [])


def check_stale_verdicts(max_age_days=30):
    """Find verdicts that haven't been reassessed in a while."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    sql = """
        SELECT 
            r.server_id,
            r.name,
            r.verdict,
            r.trust_score,
            r.scan_count
        FROM mcp_server_registry r
        WHERE r.verdict IN ('TRUSTED_RESEARCH', 'ENTERPRISE_CONTROLLED')
          AND r.scan_count < 5
        ORDER BY r.scan_count ASC
        LIMIT 50
    """
    result = ws_query(sql)
    return result.get('rows', [])


def record_audit_event(event_type, action, target_server_id, details, outcome='success'):
    """Record an audit event for quality tracking."""
    event_id = f"audit_{int(time.time()*1000)}"
    try:
        ws_write('audit_log', {
            'event_id': event_id,
            'event_type': event_type,
            'actor': SERVICE_NAME,
            'action': action,
            'target_server_id': target_server_id,
            'details_json': details,
            'outcome': outcome,
            'timestamp': utc_now_iso(),
            'immutable': True
        })
    except Exception as e:
        log.warning(f"Failed to record audit event: {e}")


def generate_audit_report(low_conf, unscored, stale):
    """Generate markdown audit report."""
    report_lines = [
        f"# Assessment Quality Audit Report",
        f"",
        f"**Generated:** {utc_now_iso()}",
        f"",
        f"## Summary",
        f"",
        f"- Low confidence verdicts: {len(low_conf)}",
        f"- Unscored servers: {len(unscored)}",
        f"- Stale verdicts: {len(stale)}",
        f"",
    ]
    
    if low_conf:
        report_lines.extend([
            f"## Low Confidence Verdicts",
            f"",
            f"| Server ID | Name | Verdict | Trust Score | Source |",
            f"|-----------|------|----------|-------------|--------|",
        ])
        for row in low_conf[:20]:
            report_lines.append(
                f"| {row.get('server_id', '')} | {row.get('name', '')[:50]} | "
                f"{row.get('verdict', '')} | {row.get('trust_score', 0)} | "
                f"{row.get('registry_source', '')} |"
            )
        report_lines.append("")
    
    if unscored:
        report_lines.extend([
            f"## Unscored Servers",
            f"",
            f"| Server ID | Name | Source |",
            f"|-----------|------|--------|",
        ])
        for row in unscored[:20]:
            report_lines.append(
                f"| {row.get('server_id', '')} | {row.get('name', '')[:50]} | "
                f"{row.get('registry_source', '')} |"
            )
        report_lines.append("")
    
    if stale:
        report_lines.extend([
            f"## Stale Verdicts Requiring Reassessment",
            f"",
            f"| Server ID | Name | Verdict | Trust Score | Scans |",
            f"|-----------|------|---------|-------------|-------|",
        ])
        for row in stale[:20]:
            report_lines.append(
                f"| {row.get('server_id', '')} | {row.get('name', '')[:50]} | "
                f"{row.get('verdict', '')} | {row.get('trust_score', 0)} | "
                f"{row.get('scan_count', 0)} |"
            )
        report_lines.append("")
    
    return "\n".join(report_lines)


def cycle():
    """Perform one audit cycle."""
    log.info("Starting assessment audit cycle")
    
    try:
        low_conf = check_low_confidence_verdicts(threshold=50, lookback_days=7)
        log.info(f"Found {len(low_conf)} low confidence verdicts")
        
        unscored = check_unscored_servers(lookback_days=7)
        log.info(f"Found {len(unscored)} unscored servers")
        
        stale = check_stale_verdicts(max_age_days=30)
        log.info(f"Found {len(stale)} stale verdicts")
        
        report = generate_audit_report(low_conf, unscored, stale)
        
        try:
            Path(AUDIT_REPORT_PATH).parent.mkdir(parents=True, exist_ok=True)
            with open(AUDIT_REPORT_PATH, 'w') as f:
                f.write(report)
            log.info(f"Audit report written to {AUDIT_REPORT_PATH}")
        except Exception as e:
            log.error(f"Failed to write audit report: {e}")
        
        for row in low_conf[:10]:
            record_audit_event(
                event_type='quality_review',
                action='flagged_low_confidence',
                target_server_id=row.get('server_id', ''),
                details={'verdict': row.get('verdict'), 'trust_score': row.get('trust_score')},
                outcome='flagged'
            )
        
        for row in unscored[:10]:
            record_audit_event(
                event_type='quality_review',
                action='flagged_unscored',
                target_server_id=row.get('server_id', ''),
                details={'source': row.get('registry_source')},
                outcome='flagged'
            )
        
        log.info("Assessment audit cycle complete")
        
    except Exception as e:
        log.error(f"Error in audit cycle: {e}", exc_info=True)


def run():
    """Main daemon run loop."""
    check_single_instance()
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    log.info(f"{SERVICE_NAME} starting")
    
    cycle()
    
    last_audit = time.time()
    
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            log.error(f"Heartbeat failed: {e}")
        
        now = time.time()
        if now - last_audit >= AUDIT_INTERVAL:
            cycle()
            last_audit = now
        
        time.sleep(HEARTBEAT_INTERVAL)


if __name__ == '__main__':
    run()