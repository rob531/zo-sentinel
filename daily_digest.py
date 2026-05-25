#!/usr/bin/env python3
"""
daily_digest.py - ZO-SENTINEL Daily Digest Generator
Runs at 07:00 UTC daily, generates comprehensive digest report.
"""
import requests
import time
import os
import signal
import sys
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

SERVICE_NAME = 'daily_digest'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
EXECUTE_URL = 'http://127.0.0.1:8773/execute'
HEARTBEAT_INTERVAL = 300
CHECK_INTERVAL = 3600

PID_FILE = '/var/run/zo/daily_digest.pid'
DIGEST_OUTPUT = '/home/workspace/zo_sentinel/DAILY_DIGEST.md'
EMAIL_ENDPOINT = 'http://api.zo.computer/zo/notify'

def ws_query(sql, params=None):
    """Execute SQL via inference_router."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(EXECUTE_URL, json=payload, timeout=60)
    resp.raise_for_status()
    return resp.json()

def ws_write(table, rows, wait=True):
    """Write rows to DuckDB via write_service."""
    url = f'{WRITE_SERVICE_URL}/write'
    payload = {'table': table, 'rows': rows, 'wait': wait}
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def send_heartbeat():
    """Send service heartbeat."""
    try:
        ws_write('service_health', {
            'service': SERVICE_NAME,
            'last_heartbeat': datetime.now(timezone.utc).isoformat(),
            'status': 'running'
        })
    except Exception as e:
        print(f"Heartbeat failed: {e}")

def check_single_instance():
    """Ensure only one instance runs."""
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
    
    def cleanup(signum, frame):
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

def get_new_mcps(since_iso):
    """Get MCPs first_seen in last 24h."""
    try:
        result = ws_query("""
            SELECT server_id, name, risk_tier, trust_score, verdict, first_seen
            FROM mcp_servers
            WHERE first_seen >= ?
            ORDER BY first_seen DESC
            LIMIT 20
        """, [since_iso])
        data = result.get('data', [])
        if data and len(data) > 1:
            columns = data[0] if isinstance(data[0], list) else []
            rows = data[1:] if isinstance(data[0], list) else data
            if columns:
                return [dict(zip(columns, row)) for row in rows]
            return rows[:20]
        return []
    except Exception as e:
        print(f"Error fetching new MCPs: {e}")
        return []

def get_verdict_changes(since_iso):
    """Get servers with verdict changes in last 24h via history."""
    try:
        result = ws_query("""
            SELECT server_id, 
                   MAX(CASE WHEN rn = 1 THEN verdict END) as newest_verdict,
                   MAX(CASE WHEN rn = 2 THEN verdict END) as previous_verdict,
                   MAX(CASE WHEN rn = 1 THEN verdict_reasoning END) as reasoning
            FROM (
                SELECT server_id, verdict, verdict_reasoning, 
                       ROW_NUMBER() OVER (PARTITION BY server_id ORDER BY last_assessed DESC) as rn
                FROM mcp_servers
                WHERE last_assessed >= ?
            ) sub
            WHERE rn <= 2
            GROUP BY server_id
            HAVING COUNT(DISTINCT verdict) > 1
        """, [since_iso])
        data = result.get('data', [])
        if data and len(data) > 1:
            columns = data[0] if isinstance(data[0], list) else []
            rows = data[1:] if isinstance(data[0], list) else data
            if columns:
                return [dict(zip(columns, row)) for row in rows]
            return rows[:20]
        return []
    except Exception as e:
        print(f"Error fetching verdict changes: {e}")
        return []

def get_new_threats(since_iso):
    """Get new threat associations in last 24h."""
    try:
        result = ws_query("""
            SELECT mta.server_id, mta.threat_type, mta.severity, mta.evidence, mta.reported_at, ms.name
            FROM mcp_threat_associations mta
            LEFT JOIN mcp_servers ms ON mta.server_id = ms.server_id
            WHERE mta.reported_at >= ?
            ORDER BY mta.reported_at DESC
            LIMIT 20
        """, [since_iso])
        data = result.get('data', [])
        if data and len(data) > 1:
            columns = data[0] if isinstance(data[0], list) else []
            rows = data[1:] if isinstance(data[0], list) else data
            if columns:
                return [dict(zip(columns, row)) for row in rows]
            return rows[:20]
        return []
    except Exception as e:
        print(f"Error fetching new threats: {e}")
        return []

def get_risk_tier_changes(since_iso):
    """Get servers with risk tier changes in last 24h."""
    try:
        result = ws_query("""
            SELECT server_id, name, risk_tier, last_assessed
            FROM mcp_servers
            WHERE last_assessed >= ?
            AND risk_tier IN ('CRITICAL', 'HIGH', 'MEDIUM')
            ORDER BY last_assessed DESC
            LIMIT 20
        """, [since_iso])
        data = result.get('data', [])
        if data and len(data) > 1:
            columns = data[0] if isinstance(data[0], list) else []
            rows = data[1:] if isinstance(data[0], list) else data
            if columns:
                return [dict(zip(columns, row)) for row in rows]
            return rows[:20]
        return []
    except Exception as e:
        print(f"Error fetching risk tier changes: {e}")
        return []

def get_top_risk_servers(limit=5):
    """Get highest risk servers by trust_score ascending."""
    try:
        result = ws_query("""
            SELECT server_id, name, risk_tier, trust_score, verdict, director_maturity_level
            FROM mcp_servers
            WHERE trust_score IS NOT NULL
            ORDER BY trust_score ASC, risk_tier DESC
            LIMIT ?
        """, [limit])
        data = result.get('data', [])
        if data and len(data) > 1:
            columns = data[0] if isinstance(data[0], list) else []
            rows = data[1:] if isinstance(data[0], list) else data
            if columns:
                return [dict(zip(columns, row)) for row in rows]
            return rows[:limit]
        return []
    except Exception as e:
        print(f"Error fetching top risk servers: {e}")
        return []

def get_pipeline_health():
    """Get pipeline health summary from service_health."""
    try:
        result = ws_query("""
            SELECT service, last_heartbeat, status
            FROM service_health
            ORDER BY service
        """)
        data = result.get('data', [])
        if data and len(data) > 1:
            columns = data[0] if isinstance(data[0], list) else []
            rows = data[1:] if isinstance(data[0], list) else data
            services = []
            if columns:
                services = [dict(zip(columns, row)) for row in rows]
            else:
                services = rows
            
            running = [s for s in services if s.get('status') == 'running']
            latest_heartbeat = max(
                (s.get('last_heartbeat', '') for s in services if s.get('last_heartbeat')),
                default='unknown'
            )
            
            return {
                'total_services': len(services),
                'running_services': len(running),
                'last_heartbeat': latest_heartbeat,
                'services': [s.get('service') for s in running]
            }
        return {'total_services': 0, 'running_services': 0, 'last_heartbeat': 'unknown'}
    except Exception as e:
        print(f"Error fetching pipeline health: {e}")
        return {'error': str(e)}

def get_build_activity_summary():
    """Get build activity from mesh_events."""
    try:
        since = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
        result = ws_query("""
            SELECT event_type, COUNT(*) as count
            FROM mesh_events
            WHERE timestamp >= ?
            GROUP BY event_type
            ORDER BY count DESC
        """, [since])
        data = result.get('data', [])
        if data and len(data) > 1:
            columns = data[0] if isinstance(data[0], list) else []
            rows = data[1:] if isinstance(data[0], list) else data
            if columns:
                return [dict(zip(columns, row)) for row in rows]
            return rows
        return []
    except Exception as e:
        print(f"Error fetching build activity: {e}")
        return []

def get_attestation_stats():
    """Get attestation statistics."""
    try:
        result = ws_query("""
            SELECT verdict, COUNT(*) as count
            FROM mcp_servers
            GROUP BY verdict
        """)
        data = result.get('data', [])
        if data and len(data) > 1:
            columns = data[0] if isinstance(data[0], list) else []
            rows = data[1:] if isinstance(data[0], list) else data
            if columns:
                return {row[0]: row[1] for row in rows}
            return {}
        return {}
    except Exception as e:
        print(f"Error fetching attestation stats: {e}")
        return {}

def send_email_notification(subject, body):
    """Send email notification via api endpoint."""
    try:
        resp = requests.post(EMAIL_ENDPOINT, json={
            'to': 'robin.craib@gmail.com',
            'subject': subject,
            'body': body[:2000]
        }, timeout=15)
        resp.raise_for_status()
        return True
    except Exception as e:
        print(f"Email notification failed: {e}")
        return False

def generate_digest():
    """Generate daily digest report."""
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(hours=24)
    since_iso = yesterday.isoformat()
    
    sections = []
    sections.append("# ZO-SENTINEL Daily Digest")
    sections.append(f"**Generated:** {now.strftime('%Y-%m-%d %H:%M UTC')}")
    sections.append(f"**Period:** {(now - timedelta(days=1)).strftime('%Y-%m-%d')} 00:00 UTC to {now.strftime('%Y-%m-%d')} 00:00 UTC")
    sections.append("")
    sections.append("---")
    sections.append("")
    
    new_mcps = get_new_mcps(since_iso)
    sections.append("## New MCPs Discovered (24h)")
    if new_mcps:
        sections.append(f"**Total:** {len(new_mcps)}")
        sections.append("")
        for mcp in new_mcps[:15]:
            name = mcp.get('name') or mcp.get('server_id', 'Unknown')
            sections.append(f"### {name}")
            sections.append(f"- **Server ID:** {mcp.get('server_id', 'N/A')}")
            sections.append(f"- **Risk Tier:** {mcp.get('risk_tier', 'UNASSESSED')}")
            score = mcp.get('trust_score')
            sections.append(f"- **Trust Score:** {f'{score:.2f}' if score is not None else 'N/A'}")
            verdict = mcp.get('verdict', 'PENDING_REVIEW')
            sections.append(f"- **Verdict:** {verdict}")
            sections.append("")
    else:
        sections.append("No new MCPs discovered in the last 24 hours.")
        sections.append("")
    
    verdict_changes = get_verdict_changes(since_iso)
    sections.append("## Verdict Changes (24h)")
    if verdict_changes:
        sections.append(f"**Total Changes:** {len(verdict_changes)}")
        sections.append("")
        for vc in verdict_changes[:10]:
            sections.append(f"- **{vc.get('name') or vc.get('server_id', 'Unknown')}**")
            sections.append(f"  - Previous: {vc.get('previous_verdict', 'N/A')}")
            sections.append(f"  - Current: {vc.get('newest_verdict', 'N/A')}")
        sections.append("")
    else:
        sections.append("No verdict changes detected.")
        sections.append("")
    
    new_threats = get_new_threats(since_iso)
    sections.append("## New Threat Intelligence (24h)")
    if new_threats:
        sections.append(f"**Total Threats:** {len(new_threats)}")
        sections.append("")
        for threat in new_threats[:15]:
            severity = threat.get('severity', 'UNKNOWN')
            severity_icon = "🔴" if severity in ['CRITICAL', 'HIGH'] else "🟡" if severity == 'MEDIUM' else "🟢"
            sections.append(f"- {severity_icon} **{threat.get('threat_type', 'Unknown')}** ({severity})")
            sections.append(f"  - Affected: {threat.get('name') or threat.get('server_id', 'N/A')}")
        sections.append("")
    else:
        sections.append("No new threat intelligence reported.")
        sections.append("")
    
    risk_tier_servers = get_risk_tier_changes(since_iso)
    sections.append("## High-Risk Servers (24h Activity)")
    if risk_tier_servers:
        sections.append(f"**High/Priority Servers with Recent Activity:** {len(risk_tier_servers)}")
        sections.append("")
        for srv in risk_tier_servers[:10]:
            tier = srv.get('risk_tier', 'UNKNOWN')
            tier_icon = "🔴" if tier == 'CRITICAL' else "🟠" if tier == 'HIGH' else "🟡"
            sections.append(f"- {tier_icon} **{srv.get('name') or srv.get('server_id', 'Unknown')}** [{tier}]")
        sections.append("")
    else:
        sections.append("No high-risk server activity.")
        sections.append("")
    
    top_risk = get_top_risk_servers(5)
    sections.append("## Top 5 Highest Risk Servers (Overall)")
    if top_risk:
        for i, server in enumerate(top_risk, 1):
            name = server.get('name') or server.get('server_id', 'Unknown')
            score = server.get('trust_score', 0)
            tier = server.get('risk_tier', 'UNKNOWN')
            verdict = server.get('verdict', 'PENDING_REVIEW')
            maturity = server.get('director_maturity_level', 'N/A')
            sections.append(f"### {i}. {name}")
            sections.append(f"| Metric | Value |")
            sections.append(f"|--------|-------|")
            sections.append(f"| Risk Tier | {tier} |")
            sections.append(f"| Trust Score | {score:.2f} |")
            sections.append(f"| Verdict | {verdict} |")
            sections.append(f"| Director Maturity | {maturity if maturity is not None else 'N/A'} |")
            sections.append("")
    else:
        sections.append("No servers with risk scores available.")
        sections.append("")
    
    pipeline = get_pipeline_health()
    sections.append("## Pipeline Health Summary")
    sections.append(f"| Service Metric | Value |")
    sections.append(f"|----------------|-------|")
    sections.append(f"| Total Services | {pipeline.get('total_services', 'Unknown')} |")
    sections.append(f"| Running Services | {pipeline.get('running_services', 'Unknown')} |")
    sections.append(f"| Last Heartbeat | {pipeline.get('last_heartbeat', 'Unknown')} |")
    if pipeline.get('services'):
        sections.append("")
        sections.append("**Active Services:**")
        for svc in pipeline.get('services', []):
            sections.append(f"- {svc}")
    sections.append("")
    
    build_events = get_build_activity_summary()
    sections.append("## Build Activity Summary (24h)")
    if build_events:
        sections.append("| Event Type | Count |")
        sections.append("|-----------|-------|")
        for event in build_events[:10]:
            event_type = event.get('event_type', 'unknown') if isinstance(event, dict) else str(event[0])
            count = event.get('count', 0) if isinstance(event, dict) else int(event[1])
            sections.append(f"| {event_type} | {count} |")
        sections.append("")
    else:
        sections.append("No build activity recorded.")
        sections.append("")
    
    attestation_stats = get_attestation_stats()
    sections.append("## Attestation Statistics")
    sections.append("| Verdict | Count |")
    sections.append("|---------|-------|")
    verdict_totals = {
        'APPROVED': 0,
        'APPROVED_WITH_CONDITIONS': 0,
        'DENIED': 0,
        'PENDING_REVIEW': 0,
        'RUG_PULL_ALERT': 0
    }
    for k, v in attestation_stats.items():
        if k in verdict_totals:
            verdict_totals[k] = v
    total_servers = sum(verdict_totals.values())
    for verdict, count in verdict_totals.items():
        pct = (count / total_servers * 100) if total_servers > 0 else 0
        sections.append(f"| {verdict} | {count} ({pct:.1f}%) |")
    sections.append("")
    
    sections.append("---")
    sections.append(f"*Generated by ZO-SENTINEL {SERVICE_NAME} at {now.isoformat()}*")
    
    return '\n'.join(sections)

def write_digest_to_file(digest_content):
    """Write digest markdown to file."""
    try:
        digest_path = Path(DIGEST_OUTPUT)
        digest_path.parent.mkdir(parents=True, exist_ok=True)
        digest_path.write_text(digest_content)
        print(f"Digest written to {DIGEST_OUTPUT}")
        return True
    except Exception as e:
        print(f"Failed to write digest to file: {e}")
        return False

def record_digest_event(digest_content, stats):
    """Record digest event in mesh_events."""
    try:
        now = datetime.now(timezone.utc)
        event_summary = f"Daily digest: {stats['new_mcps']} new MCPs, {stats['verdict_changes']} verdict changes, {stats['threats']} new threats"
        
        ws_write('mesh_events', {
            'event_type': 'daily_digest',
            'timestamp': now.isoformat(),
            'summary': event_summary,
            'details': json.dumps({
                'new_mcps': stats['new_mcps'],
                'verdict_changes': stats['verdict_changes'],
                'threats': stats['threats'],
                'high_risk_servers': stats['high_risk_servers']
            })
        })
        print("Digest event recorded in mesh_events")
    except Exception as e:
        print(f"Failed to record digest event: {e}")

def should_run_now():
    """Check if we should generate digest now (hour == 7 UTC)."""
    now = datetime.now(timezone.utc)
    return now.hour == 7

def cycle():
    """Main work cycle."""
    if should_run_now():
        print(f"Generating daily digest at {datetime.now(timezone.utc).isoformat()}")
        
        digest_content = generate_digest()
        
        write_digest_to_file(digest_content)
        
        stats = {
            'new_mcps': 0,
            'verdict_changes': 0,
            'threats': 0,
            'high_risk_servers': 0
        }
        
        new_mcps = get_new_mcps((datetime.now(timezone.utc) - timedelta(hours=24)).isoformat())
        stats['new_mcps'] = len(new_mcps)
        
        threats = get_new_threats((datetime.now(timezone.utc) - timedelta(hours=24)).isoformat())
        stats['threats'] = len(threats)
        
        record_digest_event(digest_content, stats)
        
        email_sent = send_email_notification(
            f"ZO-SENTINEL Daily Digest - {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            digest_content[:2000]
        )
        print(f"Email notification {'sent' if email_sent else 'failed'}")
    else:
        current_hour = datetime.now(timezone.utc).hour
        print(f"Waiting for 07:00 UTC (current: {current_hour:02d}:00 UTC)")
    
    send_heartbeat()

def run():
    """Main daemon run loop."""
    print(f"Starting {SERVICE_NAME} daemon...")
    check_single_instance()
    send_heartbeat()
    
    print(f"Heartbeat interval: {HEARTBEAT_INTERVAL}s")
    print(f"Check interval: {CHECK_INTERVAL}s")
    print(f"Digest output: {DIGEST_OUTPUT}")
    
    while True:
        try:
            cycle()
        except Exception as e:
            print(f"Error in cycle: {e}")
            send_heartbeat()
        time.sleep(CHECK_INTERVAL)

if __name__ == '__main__':
    run()