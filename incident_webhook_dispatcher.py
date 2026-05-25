#!/usr/bin/env python3
"""
incident_webhook_dispatcher.py -- ZO-SENTINEL incident webhook dispatcher daemon.
Polls mcp_threat_associations for HIGH/CRITICAL threats and fires webhooks to configured URLs.
"""
import os
import sys
import time
import signal
import json
import logging
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional, Set
from urllib.parse import urlparse

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)

# Service configuration
SERVICE_NAME = 'incident_webhook_dispatcher'
PORT = 8783
WRITE_SERVICE_URL = os.environ.get('WRITE_SERVICE_URL', 'http://127.0.0.1:8772/write')
EXECUTE_URL = os.environ.get('EXECUTE_URL', 'http://127.0.0.1:8772/execute')
QUERY_URL = os.environ.get('QUERY_URL', 'http://127.0.0.1:8772/query')
HEARTBEAT_INTERVAL = int(os.environ.get('HEARTBEAT_INTERVAL', '60'))
POLL_INTERVAL = int(os.environ.get('POLL_INTERVAL', '30'))
PID_FILE = f'/tmp/zo_sentinel_{SERVICE_NAME}.pid'
STATE_FILE = f'/tmp/zo_sentinel_{SERVICE_NAME}_last_check.json'

# Severity tiers for webhooks
SEVERITY_TIERS = {
    'CRITICAL': ['WEBHOOK_CRITICAL_URL', 'WEBHOOK_URL'],
    'HIGH': ['WEBHOOK_HIGH_URL', 'WEBHOOK_URL'],
}

# Retry configuration
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2
RETRY_MAX_DELAY = 60


def get_write_url() -> str:
    return f'{WRITE_SERVICE_URL}/write'


def get_execute_url() -> str:
    return f'{EXECUTE_URL}/execute'


def get_query_url() -> str:
    return f'{QUERY_URL}/query'


def check_single_instance() -> bool:
    """Check if another instance is already running."""
    pid_file = PID_FILE
    if os.path.exists(pid_file):
        try:
            with open(pid_file, 'r') as f:
                old_pid = int(f.read().strip())
            if os.path.exists(f'/proc/{old_pid}'):
                log.error(f'Another instance already running with PID {old_pid}')
                return False
            else:
                log.warning(f'Stale PID file found, removing')
                os.remove(pid_file)
        except (ValueError, IOError) as e:
            log.warning(f'Error reading PID file: {e}')
            try:
                os.remove(pid_file)
            except IOError:
                pass
    try:
        with open(pid_file, 'w') as f:
            f.write(str(os.getpid()))
        return True
    except IOError as e:
        log.error(f'Cannot create PID file: {e}')
        return False


def remove_pid_file():
    """Remove the PID file on exit."""
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except IOError:
        pass


def signal_handler(signum, frame):
    """Handle shutdown signals gracefully."""
    log.info(f'Received signal {signum}, shutting down...')
    remove_pid_file()
    sys.exit(0)


def ws_query(sql: str, params: Optional[List] = None) -> dict:
    """Execute SQL query against DuckDB via write_service."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(get_query_url(), json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_write(table: str, rows: dict, wait: bool = True) -> dict:
    """Write rows to DuckDB table via write_service."""
    url = get_write_url()
    payload = {'table': table, 'rows': rows, 'wait': wait}
    resp = requests.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_execute(sql: str) -> dict:
    """Execute DDL/DML via write_service."""
    payload = {'sql': sql}
    resp = requests.post(get_execute_url(), json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def send_heartbeat():
    """Send heartbeat to service_health table."""
    try:
        ws_write('service_health', {
            'service': SERVICE_NAME,
            'last_heartbeat': datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        log.warning(f'Failed to send heartbeat: {e}')


def get_last_check_time() -> datetime:
    """Get the last check timestamp from state file."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, 'r') as f:
                data = json.load(f)
            last_check = data.get('last_check')
            if last_check:
                return datetime.fromisoformat(last_check)
        except (json.JSONDecodeError, ValueError, IOError) as e:
            log.warning(f'Error reading state file: {e}')
    return datetime(2000, 1, 1, tzinfo=timezone.utc)


def save_last_check_time(ts: datetime):
    """Save the last check timestamp to state file."""
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump({'last_check': ts.isoformat()}, f)
    except IOError as e:
        log.warning(f'Error saving state file: {e}')


def get_webhook_configured() -> Dict[str, str]:
    """Get webhook URLs from environment variables."""
    urls = {
        'CRITICAL': os.environ.get('WEBHOOK_CRITICAL_URL', ''),
        'HIGH': os.environ.get('WEBHOOK_HIGH_URL', ''),
        'DEFAULT': os.environ.get('WEBHOOK_URL', ''),
    }
    return {k: v for k, v in urls.items() if v}


def fetch_new_high_risk_threats(since: datetime) -> List[Dict[str, Any]]:
    """Fetch new HIGH or CRITICAL threats since last check."""
    sql = f"""
        SELECT 
            ta.id,
            ta.server_id,
            ta.threat_type,
            ta.severity,
            ta.evidence,
            ta.reported_at,
            r.name as server_name,
            r.url as server_url
        FROM mcp_threat_associations ta
        LEFT JOIN mcp_server_registry r ON ta.server_id = r.server_id
        WHERE ta.reported_at > '{since.isoformat()}'
        AND ta.severity IN ('CRITICAL', 'HIGH')
        ORDER BY ta.reported_at DESC
    """
    try:
        result = ws_query(sql)
        return result.get('rows', [])
    except Exception as e:
        log.error(f'Failed to fetch new threats: {e}')
        return []


def dispatch_webhook(url: str, payload: Dict[str, Any], severity: str) -> bool:
    """Dispatch webhook with exponential backoff retry."""
    if not url:
        return False
    
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': f'ZO-SENTINEL/{SERVICE_NAME}',
        'X-Sentinel-Severity': severity,
        'X-Sentinel-Timestamp': datetime.now(timezone.utc).isoformat(),
    }
    
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                url,
                json=payload,
                headers=headers,
                timeout=30
            )
            if resp.status_code < 500:
                if resp.status_code < 400:
                    log.info(f'Webhook dispatched successfully to {url} (status {resp.status_code})')
                    return True
                else:
                    log.warning(f'Webhook rejected by {url} (status {resp.status_code})')
                    return False
            else:
                log.warning(f'Webhook server error at {url} (status {resp.status_code})')
        except requests.RequestException as e:
            log.warning(f'Webhook request failed to {url}: {e}')
        
        if attempt < MAX_RETRIES - 1:
            delay = min(RETRY_BASE_DELAY * (2 ** attempt), RETRY_MAX_DELAY)
            log.info(f'Retrying webhook to {url} in {delay}s (attempt {attempt + 1}/{MAX_RETRIES})')
            time.sleep(delay)
    
    log.error(f'Failed to dispatch webhook to {url} after {MAX_RETRIES} attempts')
    return False


def log_incident(server_id: str, threat_type: str, severity: str, action: str, detail: str):
    """Log incident to audit_log table."""
    try:
        ws_write('audit_log', {
            'target_server_id': server_id,
            'event_type': f'incident_webhook_{action}',
            'actor': SERVICE_NAME,
            'detail': json.dumps({
                'threat_type': threat_type,
                'severity': severity,
                'detail': detail,
                'timestamp': datetime.now(timezone.utc).isoformat(),
            }),
            'created_at': datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        log.error(f'Failed to log incident: {e}')


def build_webhook_payload(threats: List[Dict[str, Any]], severity: str) -> Dict[str, Any]:
    """Build webhook payload for threat batch."""
    return {
        'event_type': 'sentinel_high_risk_threats',
        'severity': severity,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'source': 'incident_webhook_dispatcher',
        'threat_count': len(threats),
        'threats': [
            {
                'id': t.get('id'),
                'server_id': t.get('server_id'),
                'server_name': t.get('server_name', 'Unknown'),
                'server_url': t.get('server_url', ''),
                'threat_type': t.get('threat_type', 'Unknown'),
                'evidence': t.get('evidence', ''),
                'reported_at': t.get('reported_at'),
            }
            for t in threats
        ],
        'summary': f"{len(threats)} {severity} threat(s) detected requiring attention",
    }


def process_threat_batch(threats: List[Dict[str, Any]], webhook_urls: Dict[str, str]):
    """Process a batch of threats and dispatch webhooks by severity."""
    by_severity: Dict[str, List[Dict[str, Any]]] = {}
    
    for threat in threats:
        severity = threat.get('severity', 'HIGH')
        if severity not in by_severity:
            by_severity[severity] = []
        by_severity[severity].append(threat)
    
    for severity, severity_threats in by_severity.items():
        url = webhook_urls.get(severity) or webhook_urls.get('DEFAULT')
        
        if url:
            payload = build_webhook_payload(severity_threats, severity)
            
            server_ids = [t.get('server_id') for t in severity_threats if t.get('server_id')]
            for server_id in server_ids:
                log_incident(
                    server_id,
                    severity_threats[0].get('threat_type', 'Unknown'),
                    severity,
                    'dispatch_attempted',
                    f'Attempting webhook dispatch to {url}'
                )
            
            success = dispatch_webhook(url, payload, severity)
            
            if success:
                for server_id in server_ids:
                    log_incident(
                        server_id,
                        severity_threats[0].get('threat_type', 'Unknown'),
                        severity,
                        'dispatch_success',
                        f'Webhook dispatched successfully'
                    )
            else:
                for server_id in server_ids:
                    log_incident(
                        server_id,
                        severity_threats[0].get('threat_type', 'Unknown'),
                        severity,
                        'dispatch_failed',
                        f'Webhook dispatch failed after {MAX_RETRIES} attempts'
                    )
        else:
            log.warning(f'No webhook URL configured for severity {severity}, logging only')


def ensure_audit_table():
    """Ensure audit_log table exists."""
    try:
        ws_execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id BIGINT PRIMARY KEY,
                target_server_id VARCHAR,
                event_type VARCHAR,
                actor VARCHAR,
                detail TEXT,
                created_at TIMESTAMPTZ DEFAULT now()
            )
        """)
    except Exception as e:
        log.warning(f'Could not ensure audit_log table: {e}')


def run():
    """Main daemon loop."""
    log.info(f'Starting {SERVICE_NAME}...')
    
    if not check_single_instance():
        log.error('Cannot start: another instance is running')
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_audit_table()
    
    last_check = get_last_check_time()
    log.info(f'Starting threat monitoring from {last_check.isoformat()}')
    
    cycle_count = 0
    start_time = time.time()
    
    while True:
        try:
            send_heartbeat()
            
            current_time = datetime.now(timezone.utc)
            new_threats = fetch_new_high_risk_threats(last_check)
            
            if new_threats:
                log.info(f'Found {len(new_threats)} new HIGH/CRITICAL threats')
                
                webhook_urls = get_webhook_configured()
                if webhook_urls:
                    process_threat_batch(new_threats, webhook_urls)
                else:
                    log.warning('No webhook URLs configured, threats logged only')
                    for threat in new_threats:
                        log_incident(
                            threat.get('server_id', 'unknown'),
                            threat.get('threat_type', 'Unknown'),
                            threat.get('severity', 'HIGH'),
                            'threat_detected',
                            json.dumps(threat)
                        )
                
                last_check = current_time
                save_last_check_time(last_check)
            else:
                log.debug('No new HIGH/CRITICAL threats detected')
            
            cycle_count += 1
            if cycle_count % 10 == 0:
                uptime = int(time.time() - start_time)
                log.info(f'Daemon uptime: {uptime}s, cycles: {cycle_count}')
            
            time.sleep(POLL_INTERVAL)
            
        except Exception as e:
            log.error(f'Error in main loop: {e}', exc_info=True)
            time.sleep(POLL_INTERVAL)


from fastapi import FastAPI
import uvicorn

app = FastAPI()

@app.get('/health')
def health():
    """Health check endpoint."""
    uptime = time.time() - os.environ.get('SERVICE_START_TIME', time.time())
    return {
        'status': 'ok',
        'service': SERVICE_NAME,
        'uptime': uptime,
    }

@app.get('/webhooks/configured')
def get_configured_webhooks():
    """List configured webhook URLs (without secrets)."""
    urls = get_webhook_configured()
    return {
        'configured': len(urls) > 0,
        'severities': list(urls.keys()),
    }

def start_server():
    """Start the FastAPI server."""
    uvicorn.run(app, host='127.0.0.1', port=PORT)

if __name__ == '__main__':
    os.environ['SERVICE_START_TIME'] = str(time.time())
    run()
    start_server()