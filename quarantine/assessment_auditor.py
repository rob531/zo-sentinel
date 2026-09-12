#!/usr/bin/env python3
"""
assessment_auditor.py -- ZO-SENTINEL Assessment Quality Auditor
Reviews recent verdicts for quality issues and writes corrections.
"""
import requests
import time
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

SERVICE_NAME = 'assessment_auditor'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
EXECUTE_URL = 'http://127.0.0.1:8772/execute'
HEARTBEAT_INTERVAL = 60
AUDIT_INTERVAL = 86400

PID_FILE = '/var/run/zo/assessment_auditor.pid'
AUDIT_REPORT_PATH = '/home/workspace/zo_sentinel/AUDIT_REPORT.md'


def ws_query(sql, params=None):
    """Execute SQL query against DuckDB via inference_router."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_write(table, rows, wait=True):
    """Write rows to DuckDB table via write_service."""
    url = f'{WRITE_SERVICE_URL}/write'
    payload = {'table': table, 'rows': rows, 'wait': wait}
    resp = requests.post(url, json=payload)
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
        print(f"Heartbeat failed: {e}")


def check_single_instance():
    """Ensure only one instance of daemon runs."""
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    
    if os.path.exists(PID_FILE):
        with open(PID_FILE) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            print(f"Already running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            pass
    
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))


def check_low_confidence_verdicts():
    """Find servers with verdict but confidence < 0.3."""
    sql = """
    SELECT id, name, verdict, confidence, last_seen
    FROM mcp_servers
    WHERE verdict IS NOT NULL
      AND verdict != ''
      AND (confidence < 0.3 OR confidence IS NULL)
    ORDER BY last_seen DESC
    """
    try:
        result = ws_query(sql)
        return result.get('rows', [])
    except Exception as e:
        print(f"Error checking low confidence verdicts: {e}")
        return []


def check_identical_signal_scores():
    """Find servers where all 6 signals have identical scores (scoring bug indicator)."""
    sql = """
    SELECT 
        server_id,
        COUNT(DISTINCT score) as unique_scores,
        COUNT(*) as signal_count,
        MIN(score) as score_value
    FROM mcp_signal_scores
    GROUP BY server_id
    HAVING COUNT(DISTINCT score) = 1 AND COUNT(*) >= 6
    """
    try:
        result = ws_query(sql)
        return result.get('rows', [])
    except Exception as e:
        print(f"Error checking identical signal scores: {e}")
        return []


def check_stale_assessments():
    """Find servers assessed > 30 days ago not reassessed."""
    threshold = datetime.now(timezone.utc) - timedelta(days=30)
    sql = """
    SELECT id, name, verdict, last_seen, status
    FROM mcp_servers
    WHERE last_seen < ?
      AND status NOT IN ('ARCHIVED', 'DEPRECATED', 'REMOVED')
    ORDER BY last_seen ASC
    """
    try:
        result = ws_query(sql, [threshold.isoformat()])
        return result.get('rows', [])
    except Exception as e:
        print(f"Error checking stale assessments: {e}")
        return []


def check_trusted_with_threats():
    """Find TRUSTED_GENERAL servers with HIGH/CRITICAL threat associations."""
    sql = """
    SELECT DISTINCT
        s.id as server_id,
        s.name as server_name,
        t.threat_type,
        t.severity,
        t.reported_at
    FROM mcp_servers s
    JOIN mcp_threat_associations t ON s.server_id = t.server_id
    WHERE s.risk_tier = 'TRUSTED_GENERAL'
      AND t.severity IN ('HIGH', 'CRITICAL')
    ORDER BY t.reported_at DESC
    """
    try:
        result = ws_query(sql)
        return result.get('rows', [])
    except Exception as e:
        print(f"Error checking trusted with threats: {e}")
        return []


def write_correction(server_id, issue_type, reason, details=None):
    """Write a correction record to the corrections table."""
    correction = {
        'agent_id': 'zo_sentinel.auditor',
        'action': 'assessment_quality_issue',
        'issue_type': issue_type,
        'reason': reason,
        'cluster': 'data_quality',
        'server_id': server_id,
        'details': details or {},
        'created_at': datetime.now(timezone.utc).isoformat()
    }
    try:
        ws_write('corrections', correction)
    except Exception as e:
        print(f"Failed to write correction: {e}")


def create_corrections_table():
    """Create corrections table if not exists."""
    sql = """
    CREATE TABLE IF NOT EXISTS corrections (
        id BIGINT PRIMARY KEY,
        agent_id VARCHAR,
        action VARCHAR,
        issue_type VARCHAR,
        reason TEXT,
        cluster VARCHAR,
        server_id VARCHAR,
        details JSON,
        created_at TIMESTAMPTZ DEFAULT now()
    )
    """
    try:
        ws_query(sql)
    except Exception as e:
        print(f"Error creating corrections table: {e}")


def generate_audit_report(issues):
    """Generate AUDIT_REPORT.md with findings."""
    report_lines = [
        "# ZO-SENTINEL Assessment Quality Audit Report",
        f"",
        f"**Generated:** {datetime.now(timezone.utc).isoformat()}",
        f"**Auditor:** {SERVICE_NAME}",
        f"",
        f"---",
        f"",
    ]
    
    total_issues = sum(len(v) for v in issues.values())
    report_lines.append(f"## Summary")
    report_lines.append(f"")
    report_lines.append(f"- **Total Issues Found:** {total_issues}")
    report_lines.append(f"- Low Confidence Verdicts: {len(issues.get('low_confidence', []))}")
    report_lines.append(f"- Identical Signal Scores: {len(issues.get('identical_scores', []))}")
    report_lines.append(f"- Stale Assessments: {len(issues.get('stale', []))}")
    report_lines.append(f"- Trusted with Threats: {len(issues.get('trusted_threats', []))}")
    report_lines.append(f"")
    report_lines.append(f"---")
    report_lines.append(f"")
    
    if issues.get('low_confidence'):
        report_lines.append(f"## 1. Low Confidence Verdicts")
        report_lines.append(f"")
        report_lines.append(f"| Server | Verdict | Confidence | Last Seen |")
        report_lines.append(f"|--------|---------|------------|-----------|")
        for s in issues['low_confidence']:
            conf = s.get('confidence') or 'NULL'
            report_lines.append(f"| {s.get('name', 'N/A')} | {s.get('verdict', 'N/A')} | {conf} | {s.get('last_seen', 'N/A')} |")
        report_lines.append(f"")
    
    if issues.get('identical_scores'):
        report_lines.append(f"## 2. Identical Signal Scores (Possible Scoring Bug)")
        report_lines.append(f"")
        report_lines.append(f"| Server ID | Unique Scores | Signal Count | Score Value |")
        report_lines.append(f"|-----------|---------------|--------------|-------------|")
        for s in issues['identical_scores']:
            report_lines.append(f"| {s.get('server_id', 'N/A')} | {s.get('unique_scores', 'N/A')} | {s.get('signal_count', 'N/A')} | {s.get('score_value', 'N/A')} |")
        report_lines.append(f"")
    
    if issues.get('stale'):
        report_lines.append(f"## 3. Stale Assessments (>30 days)")
        report_lines.append(f"")
        report_lines.append(f"| Server | Verdict | Last Seen | Status |")
        report_lines.append(f"|--------|---------|-----------|--------|")
        for s in issues['stale']:
            report_lines.append(f"| {s.get('name', 'N/A')} | {s.get('verdict', 'N/A')} | {s.get('last_seen', 'N/A')} | {s.get('status', 'N/A')} |")
        report_lines.append(f"")
    
    if issues.get('trusted_threats'):
        report_lines.append(f"## 4. TRUSTED_GENERAL with HIGH/CRITICAL Threats (Contradiction)")
        report_lines.append(f"")
        report_lines.append(f"| Server | Threat Type | Severity | Reported At |")
        report_lines.append(f"|--------|--------------|----------|-------------|")
        for t in issues['trusted_threats']:
            report_lines.append(f"| {t.get('server_name', 'N/A')} | {t.get('threat_type', 'N/A')} | {t.get('severity', 'N/A')} | {t.get('reported_at', 'N/A')} |")
        report_lines.append(f"")
    
    if total_issues == 0:
        report_lines.append(f"## No Issues Found")
        report_lines.append(f"")
        report_lines.append(f"All assessment quality checks passed. No corrective actions required.")
        report_lines.append(f"")
    
    report_lines.append(f"---")
    report_lines.append(f"*Report generated by {SERVICE_NAME}*")
    
    report_content = '\n'.join(report_lines)
    
    try:
        Path(AUDIT_REPORT_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(AUDIT_REPORT_PATH, 'w') as f:
            f.write(report_content)
        print(f"Audit report written to {AUDIT_REPORT_PATH}")
    except Exception as e:
        print(f"Failed to write audit report: {e}")
    
    return report_content


def cycle():
    """Main audit cycle - review recent verdicts for quality issues."""
    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting assessment audit cycle")
    
    create_corrections_table()
    
    issues = {
        'low_confidence': [],
        'identical_scores': [],
        'stale': [],
        'trusted_threats': []
    }
    
    low_conf = check_low_confidence_verdicts()
    issues['low_confidence'] = low_conf
    for s in low_conf:
        reason = f"Server '{s.get('name')}' has verdict '{s.get('verdict')}' but confidence is {s.get('confidence') or 'NULL'} (<0.3 threshold)"
        write_correction(s.get('id'), 'low_confidence_verdict', reason, {
            'verdict': s.get('verdict'),
            'confidence': s.get('confidence')
        })
    print(f"  Found {len(low_conf)} low confidence verdicts")
    
    identical = check_identical_signal_scores()
    issues['identical_scores'] = identical
    for s in identical:
        reason = f"Server '{s.get('server_id')}' has all signals scoring {s.get('score_value')} - possible scoring bug"
        write_correction(s.get('server_id'), 'identical_signal_scores', reason, {
            'score_value': s.get('score_value'),
            'signal_count': s.get('signal_count')
        })
    print(f"  Found {len(identical)} servers with identical signal scores")
    
    stale = check_stale_assessments()
    issues['stale'] = stale
    for s in stale:
        reason = f"Server '{s.get('name')}' has not been reassessed since {s.get('last_seen')} (>30 days)"
        write_correction(s.get('id'), 'stale_assessment', reason, {
            'last_seen': s.get('last_seen'),
            'status': s.get('status')
        })
    print(f"  Found {len(stale)} stale assessments")
    
    trusted_threats = check_trusted_with_threats()
    issues['trusted_threats'] = trusted_threats
    for t in trusted_threats:
        reason = f"Server '{t.get('server_name')}' is TRUSTED_GENERAL but has {t.get('severity')} threat '{t.get('threat_type')}' - classification contradiction"
        write_correction(t.get('server_id'), 'trusted_threat_contradiction', reason, {
            'threat_type': t.get('threat_type'),
            'severity': t.get('severity'),
            'reported_at': t.get('reported_at')
        })
    print(f"  Found {len(trusted_threats)} TRUSTED_GENERAL with threats")
    
    generate_audit_report(issues)
    
    total = sum(len(v) for v in issues.values())
    print(f"[{datetime.now(timezone.utc).isoformat()}] Audit cycle complete: {total} issues found")
    
    return issues


def run():
    """Run the assessment auditor daemon."""
    print(f"Starting {SERVICE_NAME}...")
    check_single_instance()
    send_heartbeat()
    
    while True:
        try:
            cycle()
        except Exception as e:
            print(f"Error in audit cycle: {e}")
        
        send_heartbeat()
        time.sleep(AUDIT_INTERVAL)


if __name__ == '__main__':
    run()