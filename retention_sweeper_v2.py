import os
import sys
import time
import json
import logging
import signal
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any, List
from pathlib import Path

import requests
import uvicorn
from fastapi import FastAPI, HTTPException

PROJECT_DIR = Path('/home/workspace/zo_sentinel')
LOG_DIR = PROJECT_DIR / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)

LOG_FILE = LOG_DIR / 'retention_sweeper_v2.log'
PID_FILE = '/tmp/retention_sweeper_v2.pid'

SERVICE_NAME = 'retention_sweeper_v2'
SERVICE_PORT = 8791
WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
QUERY_URL = 'http://127.0.0.1:8772/query'
EXECUTE_URL = 'http://127.0.0.1:8772/execute'
WRITE_URL = 'http://127.0.0.1:8772/write'

HEARTBEAT_INTERVAL = 60
EVIDENCE_EXPIRY_DAYS = 30
POLL_SECS = 300
MAX_RETRIES = 5
BASE_BACKOFF = 2.0

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger(SERVICE_NAME)

app = FastAPI()
start_time = time.time()

app_state: Dict[str, Any] = {
    'evidence_expired_count': 0,
    'cycles_run': 0,
    'last_cycle': None,
    'errors': []
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ws_query(sql: str, params: Optional[List] = None) -> Dict[str, Any]:
    payload = {'sql': sql}
    if params:
        payload['params'] = params
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(QUERY_URL, json=payload, timeout=30)
            if resp.status_code >= 500:
                backoff = BASE_BACKOFF ** attempt
                log.warning(f"Query 5xx on attempt {attempt+1}, backing off {backoff}s")
                time.sleep(backoff)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            log.error(f"Query error: {e}")
            if attempt == MAX_RETRIES - 1:
                return {'rows': [], 'error': str(e)}
            time.sleep(BASE_BACKOFF ** attempt)
    return {'rows': [], 'error': 'Max retries exceeded'}


def ws_write(table: str, rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload = {'table': table, 'rows': rows, 'wait': True}
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(WRITE_URL, json=payload, timeout=30)
            if resp.status_code >= 500:
                backoff = BASE_BACKOFF ** attempt
                log.warning(f"Write 5xx on attempt {attempt+1}, backing off {backoff}s")
                time.sleep(backoff)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            log.error(f"Write error: {e}")
            if attempt == MAX_RETRIES - 1:
                return {'ok': False, 'error': str(e)}
            time.sleep(BASE_BACKOFF ** attempt)
    return {'ok': False, 'error': 'Max retries exceeded'}


def ws_execute(sql: str) -> Dict[str, Any]:
    payload = {'sql': sql}
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
            if resp.status_code >= 500:
                backoff = BASE_BACKOFF ** attempt
                log.warning(f"Execute 5xx on attempt {attempt+1}, backing off {backoff}s")
                time.sleep(backoff)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            log.error(f"Execute error: {e}")
            if attempt == MAX_RETRIES - 1:
                return {'ok': False, 'error': str(e)}
            time.sleep(BASE_BACKOFF ** attempt)
    return {'ok': False, 'error': 'Max retries exceeded'}


def check_single_instance() -> bool:
    pid_path = Path(PID_FILE)
    if pid_path.exists():
        try:
            existing_pid = int(pid_path.read_text().strip())
            if os.path.exists(f'/proc/{existing_pid}'):
                log.error(f"Another instance running with PID {existing_pid}")
                return False
            else:
                log.info(f"Stale PID file found, removing")
                pid_path.unlink(missing_ok=True)
        except (ValueError, IOError) as e:
            log.warning(f"Error reading PID file: {e}")
            pid_path.unlink(missing_ok=True)
    current_pid = os.getpid()
    pid_path.write_text(str(current_pid))
    log.info(f"PID {current_pid} acquired")
    return True


def remove_pid_file():
    try:
        Path(PID_FILE).unlink(missing_ok=True)
        log.info("PID file removed")
    except IOError as e:
        log.warning(f"Failed to remove PID file: {e}")


def signal_handler(signum, frame):
    sig_name = signal.Signals(signum).name
    log.info(f"Received {sig_name}, shutting down gracefully")
    remove_pid_file()
    sys.exit(0)


def send_heartbeat():
    try:
        payload = {
            'service': SERVICE_NAME,
            'last_heartbeat': utc_now_iso()
        }
        result = ws_write('service_health', [payload])
        if result.get('ok'):
            log.debug("Heartbeat sent")
        else:
            log.warning(f"Heartbeat failed: {result}")
    except Exception as e:
        log.error(f"Heartbeat error: {e}")


def ensure_tables():
    result = ws_execute("""
        CREATE SEQUENCE IF NOT EXISTS retention_events_id_seq
    """)
    
    result = ws_execute("""
        CREATE TABLE IF NOT EXISTS retention_events (
            id INTEGER PRIMARY KEY DEFAULT nextval('retention_events_id_seq'),
            server_id VARCHAR(255),
            event_type VARCHAR(50),
            evidence_field VARCHAR(100),
            expired_at TIMESTAMP,
            marked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            status VARCHAR(20) DEFAULT 'pending'
        )
    """)
    log.info("Retention events table ensured")


def get_stale_evidence_servers() -> List[Dict[str, Any]]:
    expiry_threshold = datetime.now(timezone.utc) - timedelta(days=EVIDENCE_EXPIRY_DAYS)
    expiry_str = expiry_threshold.strftime('%Y-%m-%d %H:%M:%S')
    
    sql = f"""
        SELECT 
            server_id,
            name,
            evidence_blob,
            registry_source,
            created_at,
            scanned_at
        FROM mcp_server_registry
        WHERE 
            (evidence_blob IS NOT NULL 
             AND evidence_blob != ''
             AND evidence_blob != '{{}}'
             AND evidence_blob != 'null')
            AND (
                (scanned_at IS NOT NULL AND scanned_at < '{expiry_str}')
                OR (scanned_at IS NULL AND created_at < '{expiry_str}')
            )
        LIMIT 500
    """
    
    result = ws_query(sql)
    return result.get('rows', [])


def get_mcp_signal_scores_with_stale_evidence() -> List[Dict[str, Any]]:
    expiry_threshold = datetime.now(timezone.utc) - timedelta(days=EVIDENCE_EXPIRY_DAYS)
    expiry_str = expiry_threshold.strftime('%Y-%m-%d %H:%M:%S')
    
    sql = f"""
        SELECT 
            s.server_id,
            r.name,
            s.signal_name,
            s.evidence,
            s.scored_at
        FROM mcp_signal_scores s
        LEFT JOIN mcp_server_registry r ON s.server_id = r.server_id
        WHERE 
            s.evidence IS NOT NULL
            AND s.evidence != ''
            AND s.scored_at < '{expiry_str}'
        LIMIT 500
    """
    
    result = ws_query(sql)
    return result.get('rows', [])


def get_mcp_fingerprints_with_stale_evidence() -> List[Dict[str, Any]]:
    expiry_threshold = datetime.now(timezone.utc) - timedelta(days=EVIDENCE_EXPIRY_DAYS)
    expiry_str = expiry_threshold.strftime('%Y-%m-%d %H:%M:%S')
    
    sql = f"""
        SELECT 
            server_id,
            tool_hash,
            schema_hash,
            permission_hash,
            computed_at
        FROM mcp_fingerprints
        WHERE 
            computed_at IS NOT NULL
            AND computed_at < '{expiry_str}'
        LIMIT 500
    """
    
    result = ws_query(sql)
    return result.get('rows', [])


def mark_evidence_for_expiry(server_id: str, field: str, expired_at: str, event_type: str = 'evidence_expiry') -> bool:
    payload = {
        'server_id': server_id,
        'event_type': event_type,
        'evidence_field': field,
        'expired_at': expired_at,
        'marked_at': utc_now_iso(),
        'status': 'marked_for_expiry'
    }
    
    result = ws_write('retention_events', [payload])
    if result.get('ok'):
        log.debug(f"Marked {server_id}.{field} for expiry")
        return True
    else:
        log.warning(f"Failed to mark {server_id}.{field}: {result}")
        return False


def clear_evidence_blob(server_id: str, field: str) -> bool:
    sql = f"""
        UPDATE mcp_server_registry
        SET evidence_blob = NULL, 
            updated_at = '{utc_now_iso()}'
        WHERE server_id = '{server_id}'
    """
    
    result = ws_execute(sql)
    if result.get('ok'):
        log.info(f"Cleared {field} for server {server_id}")
        return True
    else:
        log.warning(f"Failed to clear {field} for {server_id}: {result}")
        return False


def clear_signal_evidence(server_id: str, signal_name: str) -> bool:
    sql = f"""
        UPDATE mcp_signal_scores
        SET evidence = NULL,
            scored_at = scored_at
        WHERE server_id = '{server_id}' AND signal_name = '{signal_name}'
    """
    
    result = ws_execute(sql)
    if result.get('ok'):
        log.info(f"Cleared evidence for {server_id}.{signal_name}")
        return True
    else:
        log.warning(f"Failed to clear signal evidence for {server_id}.{signal_name}: {result}")
        return False


def clear_fingerprint_evidence(server_id: str) -> bool:
    sql = f"""
        UPDATE mcp_fingerprints
        SET tool_hash = NULL,
            schema_hash = NULL,
            computed_at = computed_at
        WHERE server_id = '{server_id}'
    """
    
    result = ws_execute(sql)
    if result.get('ok'):
        log.info(f"Cleared fingerprint evidence for server {server_id}")
        return True
    else:
        log.warning(f"Failed to clear fingerprint for {server_id}: {result}")
        return False


def process_stale_registry_evidence():
    servers = get_stale_evidence_servers()
    expired_count = 0
    
    for server in servers:
        server_id = server.get('server_id')
        scanned_at = server.get('scanned_at') or server.get('created_at')
        
        if scanned_at:
            expired_count += 1
            mark_evidence_for_expiry(
                server_id,
                'evidence_blob',
                scanned_at,
                'evidence_blob_expiry'
            )
            clear_evidence_blob(server_id, 'evidence_blob')
    
    return expired_count


def process_stale_signal_evidence():
    signals = get_mcp_signal_scores_with_stale_evidence()
    cleared_count = 0
    
    for signal in signals:
        server_id = signal.get('server_id')
        signal_name = signal.get('signal_name')
        scored_at = signal.get('scored_at')
        
        if server_id and signal_name:
            cleared_count += 1
            mark_evidence_for_expiry(
                server_id,
                f'signal_evidence_{signal_name}',
                scored_at,
                'signal_evidence_expiry'
            )
            clear_signal_evidence(server_id, signal_name)
    
    return cleared_count


def process_stale_fingerprint_evidence():
    fingerprints = get_mcp_fingerprints_with_stale_evidence()
    cleared_count = 0
    
    for fp in fingerprints:
        server_id = fp.get('server_id')
        computed_at = fp.get('computed_at')
        
        if server_id:
            cleared_count += 1
            mark_evidence_for_expiry(
                server_id,
                'fingerprint_evidence',
                computed_at,
                'fingerprint_evidence_expiry'
            )
            clear_fingerprint_evidence(server_id)
    
    return cleared_count


def get_retention_stats() -> Dict[str, Any]:
    result = ws_query("""
        SELECT 
            event_type,
            COUNT(*) as count,
            MIN(marked_at) as first_marked,
            MAX(marked_at) as last_marked
        FROM retention_events
        GROUP BY event_type
    """)
    return {'events': result.get('rows', [])}


def cycle():
    log.info(f"Starting retention sweep cycle (expiry window: {EVIDENCE_EXPIRY_DAYS} days)")
    
    try:
        registry_expired = process_stale_registry_evidence()
        log.info(f"Processed {registry_expired} stale registry evidence records")
        
        signal_expired = process_stale_signal_evidence()
        log.info(f"Processed {signal_expired} stale signal evidence records")
        
        fingerprint_expired = process_stale_fingerprint_evidence()
        log.info(f"Processed {fingerprint_expired} stale fingerprint evidence records")
        
        total_expired = registry_expired + signal_expired + fingerprint_expired
        app_state['evidence_expired_count'] += total_expired
        app_state['last_cycle'] = utc_now_iso()
        app_state['cycles_run'] += 1
        
        stats = get_retention_stats()
        log.info(f"Retention stats: {json.dumps(stats)}")
        
        return total_expired
        
    except Exception as e:
        log.error(f"Error in retention cycle: {e}", exc_info=True)
        app_state['errors'].append({'time': utc_now_iso(), 'error': str(e)})
        return 0


def run():
    log.info(f"Starting {SERVICE_NAME} daemon")
    log.info(f"Evidence expiry window: {EVIDENCE_EXPIRY_DAYS} days")
    log.info(f"Poll interval: {POLL_SECS} seconds")
    
    if not check_single_instance():
        log.error("Cannot acquire PID lock, exiting")
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_tables()
    
    log.info("Running initial sweep on startup")
    cycle()
    send_heartbeat()
    
    log.info(f"Entering main loop (poll every {POLL_SECS}s)")
    
    while True:
        try:
            time.sleep(POLL_SECS)
            cycle()
            send_heartbeat()
        except Exception as e:
            log.error(f"Main loop error: {e}", exc_info=True)
            app_state['errors'].append({'time': utc_now_iso(), 'error': str(e)})
            time.sleep(POLL_SECS)


@app.get('/health')
def health():
    uptime = int(time.time() - start_time)
    return {
        'status': 'ok',
        'service': SERVICE_NAME,
        'uptime': uptime,
        'evidence_expired_count': app_state['evidence_expired_count'],
        'cycles_run': app_state['cycles_run'],
        'last_cycle': app_state['last_cycle'],
        'expiry_window_days': EVIDENCE_EXPIRY_DAYS,
        'errors': app_state['errors'][-10:] if app_state['errors'] else []
    }


@app.get('/stats')
def stats():
    return get_retention_stats()


@app.post('/trigger')
def trigger():
    count = cycle()
    send_heartbeat()
    return {'ok': True, 'expired_count': count}


def main():
    run()


if __name__ == '__main__':
    run()
    uvicorn.run(app, host='127.0.0.1', port=SERVICE_PORT, log_level='info')