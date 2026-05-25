#!/usr/bin/env python3
"""
pipeline_health.py -- ZO-SENTINEL pipeline health monitoring daemon.
Monitors assessment pipeline health and reports issues to mesh_events.
"""
import requests
import time
import os
import signal
import sys
from datetime import datetime, timezone, timedelta

SERVICE_NAME = 'pipeline_health'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
EXECUTE_URL = 'http://127.0.0.1:8773/execute'
HEARTBEAT_INTERVAL = 60
CYCLE_INTERVAL = 14400

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

def send_heartbeat(service_name=SERVICE_NAME):
    """Send service heartbeat to service_health table."""
    try:
        ws_write('service_health', {
            'service': service_name,
            'last_heartbeat': datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        print(f"Heartbeat failed: {e}")

def cleanup(signum, frame):
    """Remove PID file on shutdown."""
    pid_file = f'/var/run/zo/{SERVICE_NAME}.pid'
    if os.path.exists(pid_file):
        os.remove(pid_file)
    sys.exit(0)

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
            sys.exit(1)
        except OSError:
            pass
    
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))
    
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)

def cycle():
    """Main health check cycle."""
    timestamp = datetime.now(timezone.utc).isoformat()
    
    unscored_count = 0
    stale_count = 0
    no_attestation_count = 0
    no_threat_intel_count = 0
    zero_signal_count = 0
    
    try:
        result = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry WHERE trust_score IS NULL")
        if result and 'results' in result and result['results']:
            unscored_count = result['results'][0].get('cnt', 0) or 0
        elif result and isinstance(result, list) and result:
            unscored_count = result[0].get('cnt', 0) or 0
    except Exception as e:
        print(f"Error counting unscored servers: {e}")
    
    try:
        seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        result = ws_query("SELECT COUNT(*) as cnt FROM mcp_server_registry WHERE last_assessed IS NULL OR last_assessed < ?", [seven_days_ago])
        if result and 'results' in result and result['results']:
            stale_count = result['results'][0].get('cnt', 0) or 0
        elif result and isinstance(result, list) and result:
            stale_count = result[0].get('cnt', 0) or 0
    except Exception as e:
        print(f"Error counting stale assessments: {e}")
    
    try:
        result = ws_query("""
            SELECT COUNT(DISTINCT r.id) as cnt 
            FROM mcp_server_registry r 
            LEFT JOIN mcp_attestations a ON r.server_id = a.server_id 
            WHERE a.id IS NULL
        """)
        if result and 'results' in result and result['results']:
            no_attestation_count = result['results'][0].get('cnt', 0) or 0
        elif result and isinstance(result, list) and result:
            no_attestation_count = result[0].get('cnt', 0) or 0
    except Exception as e:
        print(f"Error counting servers without attestation: {e}")
    
    try:
        result = ws_query("""
            SELECT COUNT(DISTINCT r.id) as cnt 
            FROM mcp_server_registry r 
            LEFT JOIN mcp_threat_associations t ON r.server_id = t.server_id 
            WHERE t.id IS NULL
        """)
        if result and 'results' in result and result['results']:
            no_threat_intel_count = result['results'][0].get('cnt', 0) or 0
        elif result and isinstance(result, list) and result:
            no_threat_intel_count = result[0].get('cnt', 0) or 0
    except Exception as e:
        print(f"Error counting servers without threat intel: {e}")
    
    try:
        result = ws_query("""
            SELECT COUNT(DISTINCT r.id) as cnt 
            FROM mcp_server_registry r 
            LEFT JOIN mcp_signal_scores s ON r.server_id = s.server_id 
            WHERE s.id IS NULL
        """)
        if result and 'results' in result and result['results']:
            zero_signal_count = result['results'][0].get('cnt', 0) or 0
        elif result and isinstance(result, list) and result:
            zero_signal_count = result[0].get('cnt', 0) or 0
    except Exception as e:
        print(f"Error counting zero-signal servers: {e}")
    
    counts = {
        'unscored_servers': unscored_count,
        'stale_assessments': stale_count,
        'no_attestation': no_attestation_count,
        'no_threat_intel': no_threat_intel_count,
        'zero_signal_servers': zero_signal_count
    }
    
    severity = 'WARNING' if any(c > 10 for c in counts.values()) else 'INFO'
    
    ws_write('mesh_events', {
        'event_type': 'pipeline_health',
        'service': SERVICE_NAME,
        'timestamp': timestamp,
        'severity': severity,
        'details': {
            'unscored_servers': unscored_count,
            'stale_assessments_7d': stale_count,
            'servers_without_attestation': no_attestation_count,
            'servers_without_threat_intel': no_threat_intel_count,
            'zero_signal_servers': zero_signal_count
        }
    })
    
    summary = (
        f"Pipeline Health Check [{timestamp}] - Severity: {severity} | "
        f"Unscored: {unscored_count} | Stale: {stale_count} | "
        f"No Attest: {no_attestation_count} | No Threat: {no_threat_intel_count} | "
        f"Zero Signal: {zero_signal_count}"
    )
    print(summary)
    
    return counts

def run():
    """Main daemon entry point."""
    print(f"Starting {SERVICE_NAME} daemon...")
    check_single_instance()
    send_heartbeat()
    
    while True:
        try:
            cycle()
        except Exception as e:
            print(f"Error in cycle: {e}")
        send_heartbeat()
        time.sleep(CYCLE_INTERVAL)

if __name__ == '__main__':
    run()