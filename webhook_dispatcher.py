#!/usr/bin/env python3
"""
webhook_dispatcher.py -- ZO-SENTINEL webhook dispatcher daemon.
Polls mesh_events for critical events and dispatches to configured webhook URLs.
"""
import os
import sys
import time
import signal
import logging
from datetime import datetime, timezone
from typing import List, Optional, Set

import requests

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
log = logging.getLogger(__name__)

SERVICE_NAME = 'webhook_dispatcher'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
EXECUTE_URL = 'http://127.0.0.1:8773/execute'
HEARTBEAT_INTERVAL = 60
RETRY_COUNT = 3
RETRY_BACKOFF = 5

CRITICAL_SEVERITIES = {'CRITICAL', 'WARNING'}
TRIGGERED_EVENT_TYPES = {'build_generation_failed', 'signal_drift_detected', 'new_threat_detected'}


def get_webhook_urls() -> List[str]:
    """Parse WEBHOOK_URLS env var into list of URLs."""
    env_urls = os.environ.get('WEBHOOK_URLS', '')
    if not env_urls:
        return []
    return [url.strip() for url in env_urls.split(',') if url.strip()]


def ws_query(sql: str, params: Optional[List] = None) -> dict:
    """Execute SQL query against DuckDB via inference_router."""
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_write(table: str, rows: dict, wait: bool = True) -> dict:
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
        log.warning(f"Heartbeat failed: {e}")


def check_single_instance():
    """Ensure only one instance of daemon runs."""
    pid_file = f'/var/run/zo/{SERVICE_NAME}.pid'
    os.makedirs(os.path.dirname(pid_file), exist_ok=True)
    
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.error(f"Already running with PID {old_pid}")
            sys.exit(1)
        except OSError:
            pass
    
    with open(pid_file, 'w') as f:
        f.write(str(os.getpid()))
    
    def cleanup(signum, frame):
        if os.path.exists(pid_file):
            os.remove(pid_file)
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, cleanup)
    signal.signal(signal.SIGINT, cleanup)


def fetch_pending_events(last_check_time: Optional[str]) -> List[dict]:
    """Fetch critical/warning events from mesh_events that need webhook dispatch."""
    severity_list = "', '".join(CRITICAL_SEVERITIES)
    event_type_list = "', '".join(TRIGGERED_EVENT_TYPES)
    
    if last_check_time:
        sql = f"""
            SELECT id, event_type, severity, payload, source, created_at
            FROM mesh_events
            WHERE severity IN ('{severity_list}')
              AND event_type IN ('{event_type_list}')
              AND created_at > '{last_check_time}'
              AND webhook_dispatched = FALSE
            ORDER BY created_at ASC
            LIMIT 100
        """
    else:
        sql = f"""
            SELECT id, event_type, severity, payload, source, created_at
            FROM mesh_events
            WHERE severity IN ('{severity_list}')
              AND event_type IN ('{event_type_list}')
              AND webhook_dispatched = FALSE
            ORDER BY created_at ASC
            LIMIT 100
        """
    
    try:
        result = ws_query(sql)
        rows = result.get('data', {}).get('rows', [])
        return rows if isinstance(rows, list) else []
    except Exception as e:
        log.error(f"Failed to fetch pending events: {e}")
        return []


def dispatch_to_webhook(url: str, payload: dict, event_id: int) -> bool:
    """Send webhook POST with retry logic."""
    headers = {'Content-Type': 'application/json', 'User-Agent': 'ZO-SENTINEL/1.0'}
    
    for attempt in range(1, RETRY_COUNT + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            if resp.status_code in (200, 201, 202, 204):
                log.info(f"Webhook delivered: event_id={event_id} url={url} status={resp.status_code}")
                return True
            else:
                log.warning(f"Webhook rejected: event_id={event_id} url={url} status={resp.status_code} attempt={attempt}")
        except requests.exceptions.RequestException as e:
            log.warning(f"Webhook error: event_id={event_id} url={url} error={e} attempt={attempt}")
        
        if attempt < RETRY_COUNT:
            time.sleep(RETRY_BACKOFF)
    
    log.error(f"Webhook FAILED after {RETRY_COUNT} attempts: event_id={event_id} url={url}")
    return False


def mark_webhook_dispatched(event_ids: List[int], success: bool):
    """Mark events as dispatched (or failed) in mesh_events."""
    if not event_ids:
        return
    
    id_list = ", ".join(str(eid) for eid in event_ids)
    dispatched_status = 'TRUE' if success else 'FALSE'
    
    try:
        ws_query(f"""
            UPDATE mesh_events
            SET webhook_dispatched = {dispatched_status},
                webhook_dispatched_at = NOW()
            WHERE id IN ({id_list})
        """)
    except Exception as e:
        log.error(f"Failed to mark events as dispatched: {e}")


def log_webhook_result(event_id: int, webhook_url: str, success: bool, error: Optional[str] = None):
    """Log webhook dispatch result to a tracking table."""
    try:
        ws_write('webhook_dispatch_log', {
            'event_id': event_id,
            'webhook_url': webhook_url,
            'success': success,
            'error_message': error,
            'dispatched_at': datetime.now(timezone.utc).isoformat()
        })
    except Exception:
        pass


def dispatch_event_to_all_webhooks(event: dict, webhook_urls: List[str]) -> bool:
    """Dispatch a single event to all configured webhook URLs."""
    event_id = event.get('id')
    event_type = event.get('event_type')
    severity = event.get('severity')
    payload = event.get('payload', {})
    source = event.get('source', 'zo_sentinel')
    created_at = event.get('created_at')
    
    webhook_payload = {
        'event_type': event_type,
        'severity': severity,
        'payload': payload,
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'source': source,
        'event_id': event_id,
        'event_created_at': created_at
    }
    
    all_success = True
    for webhook_url in webhook_urls:
        success = dispatch_to_webhook(webhook_url, webhook_payload, event_id)
        if not success:
            all_success = False
        log_webhook_result(event_id, webhook_url, success)
    
    return all_success


def ensure_webhook_log_table():
    """Create webhook dispatch log table if it doesn't exist."""
    try:
        ws_query("""
            CREATE TABLE IF NOT EXISTS webhook_dispatch_log (
                id BIGINT DEFAULT nextval('webhook_log_seq'),
                event_id BIGINT,
                webhook_url VARCHAR,
                success BOOLEAN,
                error_message TEXT,
                dispatched_at TIMESTAMPTZ,
                PRIMARY KEY (id)
            )
        """)
    except Exception:
        pass


def ensure_mesh_events_table():
    """Ensure mesh_events table has webhook_dispatched column."""
    try:
        ws_query("""
            ALTER TABLE mesh_events ADD COLUMN IF NOT EXISTS webhook_dispatched BOOLEAN DEFAULT FALSE
        """)
    except Exception:
        pass
    
    try:
        ws_query("""
            ALTER TABLE mesh_events ADD COLUMN IF NOT EXISTS webhook_dispatched_at TIMESTAMPTZ
        """)
    except Exception:
        pass


def cycle():
    """Main work cycle - poll and dispatch critical events."""
    webhook_urls = get_webhook_urls()
    
    if not webhook_urls:
        log.debug("No WEBHOOK_URLS configured, skipping webhook dispatch")
        return
    
    last_check_time = None
    try:
        result = ws_query("""
            SELECT MAX(webhook_dispatched_at) as last_check
            FROM mesh_events
            WHERE webhook_dispatched = TRUE
        """)
        rows = result.get('data', {}).get('rows', [])
        if rows and rows[0].get('last_check'):
            last_check_time = rows[0]['last_check']
    except Exception as e:
        log.debug(f"Could not determine last check time: {e}")
    
    events = fetch_pending_events(last_check_time)
    
    if not events:
        log.debug("No pending critical events to dispatch")
        return
    
    log.info(f"Found {len(events)} events to dispatch to {len(webhook_urls)} webhooks")
    
    for event in events:
        dispatch_event_to_all_webhooks(event, webhook_urls)
        mark_webhook_dispatched([event.get('id')], True)


def run():
    """Main daemon entry point."""
    check_single_instance()
    log.info(f"Starting {SERVICE_NAME}")
    
    webhook_urls = get_webhook_urls()
    if not webhook_urls:
        log.warning("WEBHOOK_URLS not configured - webhook dispatcher will not send alerts")
    else:
        log.info(f"Configured with {len(webhook_urls)} webhook URLs")
        for url in webhook_urls:
            log.info(f"  -> {url}")
    
    ensure_webhook_log_table()
    ensure_mesh_events_table()
    
    send_heartbeat()
    
    while True:
        try:
            cycle()
        except Exception as e:
            log.error(f"Error in cycle: {e}")
        
        send_heartbeat()
        time.sleep(HEARTBEAT_INTERVAL)


if __name__ == '__main__':
    run()