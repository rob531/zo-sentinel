import os
import sys
import time
import signal
import logging
from datetime import datetime, timezone

LOG_DIR = os.environ.get('ZO_SENTINEL_LOGS', '/home/workspace/zo_sentinel/logs')
LOG_DIR = os.path.join(LOG_DIR, 'wirer')
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE = os.path.join(LOG_DIR, 'scanner_fingerprint_wirer.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)
log = logging.getLogger('scanner_fingerprint_wirer')

SERVICE_NAME = 'scanner_fingerprint_wirer'
SERVICE_PORT = 8799
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772/query'
EXECUTE_SERVICE_URL = 'http://127.0.0.1:8772/execute'
HEARTBEAT_INTERVAL = 60
POLL_SECS = 30

try:
    from mcp_traffic_fingerprints import detect_mcp_methods, is_mcp_traffic, extract_session_indicators
except ImportError:
    log.error('mcp_traffic_fingerprints not importable - verify library lands and smokes clean')
    detect_mcp_methods = None
    is_mcp_traffic = None
    extract_session_indicators = None


def check_single_instance():
    pid = os.getpid()
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = int(f.read().strip())
        try:
            os.kill(old_pid, 0)
            log.error('Another instance running with PID %d - exiting', old_pid)
            sys.exit(1)
        except OSError:
            log.info('Stale PID file found, replacing')
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))
    log.info('Acquired PID file: %s', PID_FILE)


def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)
        log.info('Removed PID file: %s', PID_FILE)


def signal_handler(signum, frame):
    sig_name = signal.Signals(signum).name
    log.info('Received signal %s - shutting down gracefully', sig_name)
    remove_pid_file()
    sys.exit(0)


def send_heartbeat():
    try:
        import requests
        payload = {
            'table': 'service_health',
            'rows': {
                'service': SERVICE_NAME,
                'last_heartbeat': datetime.now(timezone.utc).isoformat()
            },
            'wait': True
        }
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=5)
        if resp.status_code in (200, 201):
            log.debug('Heartbeat sent successfully')
        else:
            log.warning('Heartbeat failed with status %d', resp.status_code)
    except Exception as e:
        log.warning('Heartbeat error: %s', e)


def ws_query(sql):
    try:
        import requests
        resp = requests.post(QUERY_SERVICE_URL, json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error('Query failed: %s', e)
        return {'rows': [], 'count': 0}


def ws_write(table, rows):
    try:
        import requests
        payload = {'table': table, 'rows': rows, 'wait': True}
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error('Write failed: %s', e)
        return {'ok': False, 'error': str(e)}


def ws_execute(sql):
    try:
        import requests
        resp = requests.post(EXECUTE_SERVICE_URL, json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error('Execute failed: %s', e)
        return {'ok': False, 'error': str(e)}


def ensure_tables():
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_traffic_fingerprint_results (
        server_id VARCHAR,
        scan_timestamp VARCHAR,
        is_mcp_traffic BOOLEAN,
        detected_methods TEXT,
        session_indicators TEXT,
        confidence_score FLOAT,
        recorded_at VARCHAR DEFAULT CURRENT_TIMESTAMP
    )
    """
    ws_execute(sql)
    log.info('Ensured mcp_traffic_fingerprint_results table exists')


def analyze_http_body(server_id, http_body, content_type=None):
    """Analyze HTTP response body for MCP protocol fingerprints."""
    if detect_mcp_methods is None:
        log.warning('Traffic fingerprint library not available')
        return None
    
    if not http_body:
        return None
    
    results = {
        'server_id': server_id,
        'scan_timestamp': datetime.now(timezone.utc).isoformat(),
        'is_mcp_traffic': False,
        'detected_methods': [],
        'session_indicators': {},
        'confidence_score': 0.0
    }
    
    try:
        if isinstance(http_body, bytes):
            body_str = http_body.decode('utf-8', errors='ignore')
        else:
            body_str = str(http_body)
        
        if is_mcp_traffic(body_str, content_type):
            results['is_mcp_traffic'] = True
        
        methods = detect_mcp_methods(body_str)
        results['detected_methods'] = methods if methods else []
        
        if extract_session_indicators:
            session_data = extract_session_indicators(body_str)
            results['session_indicators'] = session_data if session_data else {}
        
        if results['is_mcp_traffic'] and results['detected_methods']:
            results['confidence_score'] = min(1.0, len(results['detected_methods']) * 0.2 + 0.3)
        
        log.info('Fingerprinted server %s: mcp=%s, methods=%s, confidence=%.2f',
                 server_id, results['is_mcp_traffic'], results['detected_methods'], results['confidence_score'])
        
    except Exception as e:
        log.error('Error analyzing HTTP body for %s: %s', server_id, e)
    
    return results


def get_unfingerprinted_candidates():
    """Get candidate servers that have been scanned but not fingerprinted."""
    sql = """
    SELECT DISTINCT r.server_id, r.name, r.url
    FROM mcp_server_registry r
    LEFT JOIN mcp_traffic_fingerprint_results t ON r.server_id = t.server_id
    WHERE t.server_id IS NULL
    AND r.verdict != 'KNOWN_THREAT'
    ORDER BY r.scan_count DESC, r.registry_source
    LIMIT 100
    """
    result = ws_query(sql)
    return result.get('rows', [])


def store_fingerprint_result(result):
    """Store fingerprint analysis result to database."""
    if not result:
        return False
    
    rows = {
        'server_id': result.get('server_id', ''),
        'scan_timestamp': result.get('scan_timestamp', ''),
        'is_mcp_traffic': result.get('is_mcp_traffic', False),
        'detected_methods': ','.join(result.get('detected_methods', [])) if result.get('detected_methods') else '',
        'session_indicators': str(result.get('session_indicators', {})),
        'confidence_score': result.get('confidence_score', 0.0)
    }
    
    ws_write('mcp_traffic_fingerprint_results', rows)
    return True


def record_audit_event(server_id, event_type, detail):
    """Record audit event for fingerprint analysis."""
    sql = """
    INSERT INTO audit_log (target_server_id, event_type, actor, detail, created_at)
    VALUES ('{server_id}', '{event_type}', '{actor}', '{detail}', '{timestamp}')
    """.format(
        server_id=server_id,
        event_type=event_type,
        actor=SERVICE_NAME,
        detail=detail.replace("'", "''"),
        timestamp=datetime.now(timezone.utc).isoformat()
    )
    ws_execute(sql)


def process_candidate_with_fingerprint(server_id, url, http_body=None, content_type=None):
    """Process a candidate server with traffic fingerprint analysis."""
    result = analyze_http_body(server_id, http_body, content_type)
    
    if result:
        store_fingerprint_result(result)
        
        event_type = 'fingerprint_mcp_confirmed' if result['is_mcp_traffic'] else 'fingerprint_scan_complete'
        detail = f"MCP confirmed: {result['is_mcp_traffic']}, methods: {result['detected_methods']}"
        record_audit_event(server_id, event_type, detail)
    
    return result


def get_scanner_recent_scans():
    """Get recent scans from mcp_scanner audit log entries."""
    sql = """
    SELECT target_server_id, detail, created_at
    FROM audit_log
    WHERE event_type LIKE 'scan_%'
    AND created_at > datetime('now', '-1 hour')
    ORDER BY created_at DESC
    LIMIT 50
    """
    return ws_query(sql)


def run_cycle():
    """Run a single fingerprint analysis cycle."""
    log.info('Starting fingerprint analysis cycle')
    
    candidates = get_unfingerprinted_candidates()
    
    if not candidates:
        log.info('No unfingerprinted candidates found, checking recent scans')
        recent_scans = get_scanner_recent_scans()
        if recent_scans.get('rows'):
            log.info('Found %d recent scanner audit entries', len(recent_scans['rows']))
        else:
            log.info('No work to do - will poll again in %d seconds', POLL_SECS)
        return 0
    
    processed = 0
    for candidate in candidates:
        server_id = candidate.get('server_id')
        name = candidate.get('name', 'unknown')
        
        try:
            result = process_candidate_with_fingerprint(server_id, None)
            
            if result and result.get('is_mcp_traffic'):
                log.info('MCP protocol confirmed for %s (%s)', name, server_id)
            
            processed += 1
            
        except Exception as e:
            log.error('Error processing candidate %s: %s', server_id, e)
    
    log.info('Fingerprint analysis cycle complete: processed %d candidates', processed)
    return processed


def heartbeat_loop():
    """Send periodic heartbeats while running."""
    while True:
        try:
            send_heartbeat()
        except Exception as e:
            log.error('Heartbeat error: %s', e)
        time.sleep(HEARTBEAT_INTERVAL)


def run():
    """Main daemon entry point."""
    log.info('Starting %s daemon', SERVICE_NAME)
    
    check_single_instance()
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_tables()
    
    import threading
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    log.info('%s initialized - starting main loop', SERVICE_NAME)
    
    while True:
        try:
            run_cycle()
        except Exception as e:
            log.error('Cycle error: %s', e)
        
        time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()