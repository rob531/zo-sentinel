#!/usr/bin/env python3
"""
alert_manager.py -- ZO-SENTINEL Alert Manager Daemon.
Polls mesh_events for critical security events and routes notifications.
"""
import requests
import time
import os
import hashlib
from datetime import datetime, timezone, timedelta
from collections import defaultdict
import json

SERVICE_NAME = 'alert_manager'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
EXECUTE_URL = 'http://127.0.0.1:8772/execute'
HEARTBEAT_INTERVAL = 60
POLL_INTERVAL = 300
ALERT_LOG_PATH = '/var/log/zo_sentinel/ALERT_LOG.md'
NOTIFY_API = 'https://api.zo.computer/zo/notify'

alert_history = {}
alert_counts = defaultdict(int)
last_hour_high_risk = []


def ws_query(sql, params=None):
    """Execute SQL query against DuckDB via inference_router."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(EXECUTE_URL, json=payload, timeout=60)
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
            'last_heartbeat': datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        print(f"Heartbeat failed: {e}")


def check_single_instance():
    """Ensure only one instance of daemon runs."""
    pid_file = f'/var/run/zo/{SERVICE_NAME}.pid'
    os.makedirs(os.path.dirname(pid_file), exist_ok=True)
    
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            print(f"Already running with PID {old_pid}")
            return False
        except OSError:
            pass
    
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))
    
    def cleanup(signum=None, frame=None):
        if os.path.exists(pid_file):
            os.remove(pid_file)
    
    import signal
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)
    return True


def get_dedup_key(server_id, event_type):
    """Generate deduplication key for alert grouping."""
    return f"{server_id}:{event_type}"


def is_duplicate(server_id, event_type, window_hours=1):
    """Check if alert is duplicate within time window."""
    key = get_dedup_key(server_id, event_type)
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    
    if key in alert_history:
        last_alert_time = alert_history[key]
        if last_alert_time > cutoff:
            return True
    
    alert_history[key] = now
    return False


def poll_mesh_events():
    """Poll mesh_events table for critical/high severity events."""
    sql = """
    SELECT me.*, ms.name as server_name, ms.verdict, ms.risk_tier, 
           ms.attestation_expires_at, ms.status as server_status
    FROM mesh_events me
    LEFT JOIN mcp_servers ms ON me.server_id = ms.server_id
    WHERE me.severity IN ('CRITICAL', 'HIGH', 'MEDIUM')
      AND me.source = 'zo_sentinel'
      AND me.created_at > NOW() - INTERVAL '5 minutes'
    ORDER BY me.created_at DESC
    """
    try:
        result = ws_query(sql)
        if result and 'results' in result:
            return result['results']
    except Exception as e:
        print(f"Error polling mesh_events: {e}")
    return []


def check_known_threat_verdicts():
    """Check for servers with new KNOWN_THREAT verdict."""
    sql = """
    SELECT server_id, name, verdict, verdict_reasoning, last_verdict_change
    FROM mcp_servers
    WHERE verdict = 'KNOWN_THREAT'
      AND last_verdict_change > NOW() - INTERVAL '5 minutes'
    """
    try:
        result = ws_query(sql)
        if result and 'results' in result:
            return result['results']
    except Exception as e:
        print(f"Error checking KNOWN_THREAT: {e}")
    return []


def check_high_risk_concentration():
    """Check for 3+ HIGH_RISK servers appearing in 1h window."""
    global last_hour_high_risk
    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)
    
    sql = """
    SELECT server_id, name, risk_tier, last_seen
    FROM mcp_servers
    WHERE risk_tier = 'HIGH_RISK'
      AND last_seen > ?
    ORDER BY last_seen DESC
    """
    try:
        result = ws_query(sql, [one_hour_ago.isoformat()])
        if result and 'results' in result:
            servers = result['results']
            last_hour_high_risk = [s for s in servers if s.get('last_seen')]
            
            if len(last_hour_high_risk) >= 3:
                return True, last_hour_high_risk
    except Exception as e:
        print(f"Error checking HIGH_RISK concentration: {e}")
    return False, []


def check_expired_attestations():
    """Check for expired attestations on deployed servers."""
    sql = """
    SELECT server_id, name, attestation_expires_at, status
    FROM mcp_servers
    WHERE attestation_expires_at < NOW()
      AND status = 'deployed'
    ORDER BY attestation_expires_at ASC
    """
    try:
        result = ws_query(sql)
        if result and 'results' in result:
            return result['results']
    except Exception as e:
        print(f"Error checking expired attestations: {e}")
    return []


def determine_severity(alert_type, data):
    """Determine alert severity based on type and data."""
    if alert_type == 'KNOWN_THREAT':
        return 'CRITICAL'
    elif alert_type == 'HIGH_RISK_CONCENTRATION':
        return 'HIGH'
    elif alert_type == 'EXPIRED_ATTESTATION':
        return 'HIGH'
    elif alert_type == 'MESH_EVENT':
        severity = data.get('severity', 'MEDIUM')
        return severity
    return 'MEDIUM'


def write_to_corrections(alert):
    """Write alert to corrections table for tracking."""
    correction_data = {
        'server_id': alert.get('server_id'),
        'correction_type': alert.get('alert_type'),
        'severity': alert.get('severity'),
        'reasoning': alert.get('message'),
        'corrected_at': datetime.now(timezone.utc).isoformat(),
        'status': 'pending'
    }
    try:
        ws_write('corrections', correction_data)
    except Exception as e:
        print(f"Failed to write to corrections: {e}")


def send_email_notification(alert):
    """Send email notification via notify API."""
    payload = {
        'subject': f"[{alert['severity']}] ZO-SENTINEL Alert: {alert['alert_type']}",
        'body': f"""
ZO-SENTINEL Security Alert
==========================

Alert Type: {alert['alert_type']}
Severity: {alert['severity']}
Server: {alert.get('server_name', alert.get('server_id', 'N/A'))}
Server ID: {alert.get('server_id', 'N/A')}

Message:
{alert.get('message', 'No additional details')}

Time: {alert.get('timestamp', datetime.now(timezone.utc).isoformat())}

--
ZO-SENTINEL Alert Manager
""",
        'priority': 'high' if alert['severity'] == 'CRITICAL' else 'normal',
        'recipients': ['security-team@zo.computer']
    }
    
    try:
        resp = requests.post(NOTIFY_API, json=payload, timeout=30)
        if resp.status_code == 200:
            print(f"Notification sent for {alert['alert_type']} - {alert.get('server_id')}")
            return True
        else:
            print(f"Notification failed: {resp.status_code}")
            return False
    except requests.exceptions.RequestException as e:
        print(f"Notification error: {e}")
        return False


def write_alert_log(alert):
    """Append entry to ALERT_LOG.md."""
    os.makedirs(os.path.dirname(ALERT_LOG_PATH), exist_ok=True)
    
    log_entry = f"""
## Alert: {alert['alert_type']}
**Timestamp:** {alert.get('timestamp', datetime.now(timezone.utc).isoformat())}
**Severity:** {alert['severity']}
**Server ID:** {alert.get('server_id', 'N/A')}
**Server Name:** {alert.get('server_name', 'N/A')}

**Message:**
{alert.get('message', 'No details')}

**Notification Sent:** {alert.get('notification_sent', False)}

---
"""
    
    try:
        with open(ALERT_LOG_PATH, 'a') as f:
            f.write(log_entry)
    except Exception as e:
        print(f"Failed to write alert log: {e}")


def process_mesh_events():
    """Process events from mesh_events table."""
    events = poll_mesh_events()
    processed = 0
    
    for event in events:
        server_id = event.get('server_id')
        event_type = event.get('event_type', 'unknown')
        
        if is_duplicate(server_id, event_type, window_hours=1):
            continue
        
        severity = event.get('severity', 'MEDIUM')
        if severity not in ('CRITICAL', 'HIGH'):
            continue
        
        alert = {
            'alert_type': f'MESH_EVENT_{event_type}',
            'severity': severity,
            'server_id': server_id,
            'server_name': event.get('server_name'),
            'message': event.get('description', f'Event type: {event_type}'),
            'timestamp': event.get('created_at', datetime.now(timezone.utc).isoformat())
        }
        
        process_alert(alert)
        processed += 1
    
    return processed


def process_known_threats():
    """Process new KNOWN_THREAT verdicts."""
    threats = check_known_threat_verdicts()
    processed = 0
    
    for threat in threats:
        server_id = threat.get('server_id')
        event_type = 'KNOWN_THREAT'
        
        if is_duplicate(server_id, event_type, window_hours=24):
            continue
        
        alert = {
            'alert_type': 'KNOWN_THREAT',
            'severity': 'CRITICAL',
            'server_id': server_id,
            'server_name': threat.get('name'),
            'message': f"New KNOWN_THREAT verdict assigned. Reasoning: {threat.get('verdict_reasoning', 'N/A')}",
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        process_alert(alert)
        processed += 1
    
    return processed


def process_high_risk_concentration():
    """Process high risk server concentration alert."""
    is_concentrated, servers = check_high_risk_concentration()
    
    if not is_concentrated:
        return 0
    
    event_type = 'HIGH_RISK_CONCENTRATION'
    
    if is_duplicate('CLUSTER', event_type, window_hours=1):
        return 0
    
    server_names = [s.get('name', s.get('server_id')) for s in servers[:3]]
    
    alert = {
        'alert_type': 'HIGH_RISK_CONCENTRATION',
        'severity': 'HIGH',
        'server_id': 'CLUSTER',
        'server_name': 'Multiple Servers',
        'message': f"{len(servers)} HIGH_RISK servers detected in 1h window: {', '.join(server_names)}",
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'affected_servers': [s.get('server_id') for s in servers]
    }
    
    process_alert(alert)
    return 1


def process_expired_attestations():
    """Process expired attestation alerts."""
    expired = check_expired_attestations()
    processed = 0
    
    for server in expired:
        server_id = server.get('server_id')
        event_type = 'EXPIRED_ATTESTATION'
        
        if is_duplicate(server_id, event_type, window_hours=24):
            continue
        
        alert = {
            'alert_type': 'EXPIRED_ATTESTATION',
            'severity': 'HIGH',
            'server_id': server_id,
            'server_name': server.get('name'),
            'message': f"Attestation expired on {server.get('attestation_expires_at')} but server is still deployed",
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        process_alert(alert)
        processed += 1
    
    return processed


def process_alert(alert):
    """Process and route a single alert."""
    print(f"Processing alert: {alert['alert_type']} - {alert.get('server_id')} [{alert['severity']}]")
    
    write_to_corrections(alert)
    
    notification_sent = send_email_notification(alert)
    alert['notification_sent'] = notification_sent
    
    write_alert_log(alert)


def cleanup_old_alerts():
    """Remove old entries from alert_history to prevent memory bloat."""
    global alert_history
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    
    alert_history = {
        k: v for k, v in alert_history.items()
        if v > cutoff
    }


def cycle():
    """Main work cycle - process all alert types."""
    print(f"[{datetime.now(timezone.utc).isoformat()}] Starting alert processing cycle")
    
    mesh_count = process_mesh_events()
    print(f"  Processed {mesh_count} mesh events")
    
    threat_count = process_known_threats()
    print(f"  Processed {threat_count} KNOWN_THREAT alerts")
    
    concentration_count = process_high_risk_concentration()
    print(f"  Processed {concentration_count} concentration alerts")
    
    attestation_count = process_expired_attestations()
    print(f"  Processed {attestation_count} attestation alerts")
    
    cleanup_old_alerts()
    
    total = mesh_count + threat_count + concentration_count + attestation_count
    print(f"[{datetime.now(timezone.utc).isoformat()}] Cycle complete: {total} alerts processed")


def run():
    """Main daemon loop."""
    if not check_single_instance():
        return
    
    send_heartbeat()
    print(f"ZO-SENTINEL Alert Manager started (PID: {os.getpid()})")
    
    while True:
        try:
            cycle()
        except Exception as e:
            print(f"Error in cycle: {e}")
        
        send_heartbeat()
        time.sleep(POLL_INTERVAL)


if __name__ == '__main__':
    run()