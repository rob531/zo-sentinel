import requests
import time
import os
import signal
import sys
import json
import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

SERVICE_NAME = 'daily_digest'
SERVICE_PORT = None
PID_FILE = '/tmp/daily_digest.pid'
WRITE_SERVICE_URL = 'http://localhost:8772'
QUERY_SERVICE_URL = 'http://localhost:8772'
EXECUTE_SERVICE_URL = 'http://localhost:8772'
DIGEST_OUTPUT = '/home/workspace/zo_sentinel/DAILY_DIGEST.md'
LOG_DIR = Path('/home/workspace/logs')
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f'{SERVICE_NAME}.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(SERVICE_NAME)

HEARTBEAT_INTERVAL = 300
POLL_SECS = 3600


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql, params=None):
    """Execute SQL query via write_service."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(f'{QUERY_SERVICE_URL}/query', json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()


def ws_write(table, rows, wait=True):
    """Write rows to DuckDB via write_service."""
    if isinstance(rows, dict):
        rows = [rows]
    payload = {'table': table, 'rows': rows, 'wait': wait}
    resp = requests.post(f'{WRITE_SERVICE_URL}/write', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql, params=None):
    """Execute DDL/DML via write_service."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(f'{EXECUTE_SERVICE_URL}/execute', json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()


def send_heartbeat():
    """Send service heartbeat to service_health table."""
    try:
        ws_write('service_health', {
            'service': SERVICE_NAME,
            'last_heartbeat': utc_now_iso(),
            'status': 'running',
            'meta': json.dumps({'last_digest_run': utc_now_iso()})
        })
    except Exception as e:
        log.warning(f"Heartbeat failed: {e}")


def check_single_instance():
    """Ensure only one instance runs via PID file."""
    pid_file = Path(PID_FILE)
    if pid_file.exists():
        with open(pid_file, 'r') as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Another instance is running (PID {old_pid}). Exiting.")
            sys.exit(1)
        except OSError:
            log.info(f"Stale PID file found for {old_pid}, removing.")
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))


def remove_pid_file():
    """Remove PID file on exit."""
    try:
        os.unlink(PID_FILE)
    except OSError:
        pass


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    log.info(f"Received signal {signum}, shutting down gracefully.")
    remove_pid_file()
    sys.exit(0)


def get_registry_summary():
    """Get MCP server registry summary from mcp_server_registry table."""
    sql = """
    SELECT 
        COUNT(*) as total_servers,
        COUNT(CASE WHEN verdict = 'TRUSTED' THEN 1 END) as trusted_count,
        COUNT(CASE WHEN verdict = 'AMBER' THEN 1 END) as amber_count,
        COUNT(CASE WHEN verdict = 'UNTRUSTED' THEN 1 END) as untrusted_count,
        COUNT(CASE WHEN verdict = 'UNKNOWN' THEN 1 END) as unknown_count,
        COUNT(CASE WHEN verdict = 'KNOWN_THREAT' THEN 1 END) as known_threat_count,
        AVG(trust_score) as avg_trust_score
    FROM mcp_server_registry
    """
    try:
        result = ws_query(sql)
        if result.get('rows') and len(result['rows']) > 0:
            return result['rows'][0]
    except Exception as e:
        log.warning(f"Failed to get registry summary: {e}")
    return {
        'total_servers': 0,
        'trusted_count': 0,
        'amber_count': 0,
        'untrusted_count': 0,
        'unknown_count': 0,
        'known_threat_count': 0,
        'avg_trust_score': 0.0
    }


def get_new_mcps(since_days=1):
    """Get newly registered MCP servers in the last N days."""
    sql = """
    SELECT server_id, name, url, verdict, trust_score, first_seen
    FROM mcp_server_registry
    WHERE first_seen >= CURRENT_TIMESTAMP - INTERVAL '1' DAY
    ORDER BY first_seen DESC
    LIMIT 50
    """
    try:
        result = ws_query(sql)
        return result.get('rows', [])
    except Exception as e:
        log.warning(f"Failed to get new MCPs: {e}")
        return []


def get_signal_scores_coverage():
    """Get signal scores coverage statistics from mcp_signal_scores table."""
    sql = """
    SELECT 
        COUNT(DISTINCT server_id) as scored_servers,
        signal_name,
        COUNT(*) as score_count,
        AVG(score) as avg_score,
        MIN(score) as min_score,
        MAX(score) as max_score
    FROM mcp_signal_scores
    GROUP BY signal_name
    """
    try:
        result = ws_query(sql)
        return result.get('rows', [])
    except Exception as e:
        log.warning(f"Failed to get signal scores coverage: {e}")
        return []


def get_risk_distribution():
    """Get risk tier distribution from mcp_risk_register table."""
    sql = """
    SELECT 
        risk_tier,
        COUNT(*) as count
    FROM mcp_risk_register
    GROUP BY risk_tier
    ORDER BY count DESC
    """
    try:
        result = ws_query(sql)
        return result.get('rows', [])
    except Exception as e:
        log.warning(f"Failed to get risk distribution: {e}")
        return []


def get_threat_associations_recent():
    """Get recent threat associations from mcp_threat_associations table."""
    sql = """
    SELECT 
        server_id,
        threat_type,
        severity,
        evidence,
        reported_at
    FROM mcp_threat_associations
    WHERE reported_at >= CURRENT_TIMESTAMP - INTERVAL '7' DAY
    ORDER BY reported_at DESC
    LIMIT 20
    """
    try:
        result = ws_query(sql)
        return result.get('rows', [])
    except Exception as e:
        log.warning(f"Failed to get recent threat associations: {e}")
        return []


def get_attestation_coverage():
    """Get attestation statistics from mcp_attestations table."""
    sql = """
    SELECT 
        COUNT(DISTINCT server_id) as attested_servers,
        COUNT(*) as total_attestations,
        COUNT(CASE WHEN expires_at < CURRENT_TIMESTAMP THEN 1 END) as expired_count,
        COUNT(CASE WHEN expires_at >= CURRENT_TIMESTAMP THEN 1 END) as active_count
    FROM mcp_attestations
    """
    try:
        result = ws_query(sql)
        if result.get('rows') and len(result['rows']) > 0:
            return result['rows'][0]
    except Exception as e:
        log.warning(f"Failed to get attestation coverage: {e}")
    return {
        'attested_servers': 0,
        'total_attestations': 0,
        'expired_count': 0,
        'active_count': 0
    }


def get_service_health_status():
    """Get service health status from service_health table."""
    sql = """
    SELECT 
        service,
        last_heartbeat,
        status,
        meta
    FROM service_health
    ORDER BY service
    """
    try:
        result = ws_query(sql)
        return result.get('rows', [])
    except Exception as e:
        log.warning(f"Failed to get service health: {e}")
        return []


def get_recent_audit_events():
    """Get recent audit events from audit_log table."""
    sql = """
    SELECT 
        event_id,
        event_type,
        actor,
        action,
        target_server_id,
        outcome,
        timestamp
    FROM audit_log
    WHERE timestamp >= CURRENT_TIMESTAMP - INTERVAL '24' HOUR
    ORDER BY timestamp DESC
    LIMIT 30
    """
    try:
        result = ws_query(sql)
        return result.get('rows', [])
    except Exception as e:
        log.warning(f"Failed to get recent audit events: {e}")
        return []


def compute_digest_id():
    """Compute deterministic ID for this digest run."""
    ts = utc_now_iso()
    content = f"{SERVICE_NAME}:{ts}"
    return hashlib.sha256(content.encode()).hexdigest()[:32]


def format_verdict_badge(verdict):
    """Format verdict with emoji indicator."""
    badges = {
        'TRUSTED': '✅',
        'AMBER': '⚠️',
        'UNTRUSTED': '❌',
        'UNKNOWN': '❓',
        'KNOWN_THREAT': '🚨'
    }
    return f"{badges.get(verdict, '❓')} {verdict}"


def format_severity(severity):
    """Format severity with color indicator."""
    colors = {
        'CRITICAL': '🔴',
        'HIGH': '🟠',
        'MEDIUM': '🟡',
        'LOW': '🟢'
    }
    return f"{colors.get(severity, '⚪')} {severity}"


def generate_digest_report():
    """Generate the daily digest report."""
    log.info("Generating daily digest report...")
    
    digest_id = compute_digest_id()
    generated_at = utc_now_iso()
    
    summary = get_registry_summary()
    new_mcps = get_new_mcps()
    signal_coverage = get_signal_scores_coverage()
    risk_dist = get_risk_distribution()
    recent_threats = get_threat_associations_recent()
    attestation_stats = get_attestation_coverage()
    service_health = get_service_health_status()
    recent_audit = get_recent_audit_events()
    
    report_lines = [
        f"# ZO-SENTINEL Daily Digest Report",
        f"",
        f"**Generated:** {generated_at}",
        f"**Digest ID:** {digest_id}",
        f"",
        f"---",
        f"",
        f"## Registry Summary",
        f"",
        f"| Metric | Value |",
        f"|---------|-------|",
        f"| Total MCP Servers | {summary.get('total_servers', 0):,} |",
        f"| Trusted | {summary.get('trusted_count', 0):,} |",
        f"| Amber | {summary.get('amber_count', 0):,} |",
        f"| Untrusted | {summary.get('untrusted_count', 0):,} |",
        f"| Unknown | {summary.get('unknown_count', 0):,} |",
        f"| Known Threats | {summary.get('known_threat_count', 0):,} |",
        f"| Avg Trust Score | {summary.get('avg_trust_score', 0):.2f} |",
        f"",
        f"---",
        f"",
        f"## Attestation Coverage",
        f"",
        f"| Metric | Value |",
        f"|---------|-------|",
        f"| Attested Servers | {attestation_stats.get('attested_servers', 0):,} |",
        f"| Total Attestations | {attestation_stats.get('total_attestations', 0):,} |",
        f"| Active | {attestation_stats.get('active_count', 0):,} |",
        f"| Expired | {attestation_stats.get('expired_count', 0):,} |",
        f"",
    ]
    
    if signal_coverage:
        report_lines.extend([
            f"---",
            f"",
            f"## Signal Scores Coverage",
            f"",
            f"| Signal | Count | Avg Score | Min | Max |",
            f"|--------|-------|----------|-----|-----|",
        ])
        for row in signal_coverage:
            report_lines.append(
                f"| {row.get('signal_name', 'unknown')} | "
                f"{row.get('score_count', 0):,} | "
                f"{row.get('avg_score', 0):.3f} | "
                f"{row.get('min_score', 0):.3f} | "
                f"{row.get('max_score', 0):.3f} |"
            )
        report_lines.append("")
    
    if risk_dist:
        report_lines.extend([
            f"---",
            f"",
            f"## Risk Distribution",
            f"",
            f"| Risk Tier | Count |",
            f"|-----------|-------|",
        ])
        for row in risk_dist:
            report_lines.append(f"| {row.get('risk_tier', 'unknown')} | {row.get('count', 0):,} |")
        report_lines.append("")
    
    if recent_threats:
        report_lines.extend([
            f"---",
            f"",
            f"## Recent Threat Associations (7 days)",
            f"",
            f"| Server | Threat Type | Severity | Reported |",
            f"|--------|-------------|----------|----------|",
        ])
        for threat in recent_threats[:10]:
            report_lines.append(
                f"| {threat.get('server_id', 'unknown')[:16]}... | "
                f"{threat.get('threat_type', 'unknown')} | "
                f"{format_severity(threat.get('severity', 'LOW'))} | "
                f"{threat.get('reported_at', 'unknown')[:10]} |"
            )
        report_lines.append("")
    
    if new_mcps:
        report_lines.extend([
            f"---",
            f"",
            f"## New MCP Registrations (24 hours)",
            f"",
            f"| Name | Verdict | Trust Score |",
            f"|------|---------|--------------|",
        ])
        for mcp in new_mcps[:15]:
            report_lines.append(
                f"| {mcp.get('name', 'unknown')[:30]} | "
                f"{format_verdict_badge(mcp.get('verdict', 'UNKNOWN'))} | "
                f"{mcp.get('trust_score', 0):.2f} |"
            )
        report_lines.append("")
    
    if service_health:
        report_lines.extend([
            f"---",
            f"",
            f"## Service Health",
            f"",
            f"| Service | Status | Last Heartbeat |",
            f"|---------|--------|----------------|",
        ])
        for svc in service_health:
            report_lines.append(
                f"| {svc.get('service', 'unknown')} | "
                f"{svc.get('status', 'unknown')} | "
                f"{svc.get('last_heartbeat', 'unknown')[:19]} |"
            )
        report_lines.append("")
    
    if recent_audit:
        report_lines.extend([
            f"---",
            f"",
            f"## Recent Audit Events (24 hours)",
            f"",
            f"| Event | Actor | Action | Outcome |",
            f"|-------|-------|--------|---------|",
        ])
        for evt in recent_audit[:15]:
            report_lines.append(
                f"| {evt.get('event_type', 'unknown')} | "
                f"{evt.get('actor', 'unknown')[:20]} | "
                f"{evt.get('action', 'unknown')[:25]} | "
                f"{evt.get('outcome', 'unknown')} |"
            )
        report_lines.append("")
    
    report_lines.extend([
        f"---",
        f"",
        f"*Report generated by ZO-SENTINEL Daily Digest ({SERVICE_NAME})*",
        f"*Digest ID: {digest_id}*"
    ])
    
    report = "\n".join(report_lines)
    
    try:
        output_path = Path(DIGEST_OUTPUT)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report)
        log.info(f"Digest report written to {DIGEST_OUTPUT}")
    except Exception as e:
        log.error(f"Failed to write digest report: {e}")
    
    return report, digest_id


def store_digest_record(digest_id, content_hash, summary):
    """Store digest run record for audit trail."""
    sql = """
    INSERT INTO audit_log (event_id, event_type, actor, action, details_json, outcome, timestamp, immutable)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    details = {
        'digest_id': digest_id,
        'content_hash': content_hash,
        'total_servers': summary.get('total_servers', 0),
        'trusted_count': summary.get('trusted_count', 0),
        'known_threat_count': summary.get('known_threat_count', 0),
        'avg_trust_score': summary.get('avg_trust_score', 0)
    }
    try:
        ws_execute(sql, params=[
            digest_id,
            'daily_digest',
            'system',
            'generate',
            json.dumps(details),
            'success',
            utc_now_iso(),
            True
        ])
    except Exception as e:
        log.warning(f"Failed to store digest record: {e}")


def cycle():
    """Execute one digest generation cycle."""
    log.info("Starting digest cycle...")
    try:
        report, digest_id = generate_digest_report()
        content_hash = hashlib.sha256(report.encode()).hexdigest()
        summary = get_registry_summary()
        store_digest_record(digest_id, content_hash, summary)
        log.info(f"Digest cycle completed successfully. ID: {digest_id}")
        return True
    except Exception as e:
        log.error(f"Digest cycle failed: {e}")
        return False


def run():
    """Main daemon loop."""
    log.info(f"Starting {SERVICE_NAME} daemon...")
    
    check_single_instance()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    log.info(f"{SERVICE_NAME} running. PID file: {PID_FILE}")
    
    cycle()
    
    while True:
        time.sleep(POLL_SECS)
        cycle()
        send_heartbeat()


if __name__ == '__main__':
    run()