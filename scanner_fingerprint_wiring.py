import sys
import time
import signal
import logging
from pathlib import Path

sys.path.insert(0, '/home/workspace/zo_sentinel')
sys.path.insert(0, '/home/workspace')

from mcp_traffic_fingerprints import (
    detect_mcp_methods,
    is_mcp_traffic,
    extract_session_indicators,
    extract_session_id,
    has_mcp_endpoint_indicator,
)

SERVICE_NAME = 'scanner_fingerprint_wiring'
SERVICE_PORT = 8791
PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772/query'
EXECUTE_SERVICE_URL = 'http://127.0.0.1:8772/execute'
HEARTBEAT_INTERVAL = 60
POLL_SECS = 30
LOG_FILE = '/tmp/scanner_fingerprint_wiring.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
log = logging.getLogger(SERVICE_NAME)


def check_single_instance():
    pid = str(os.getpid())
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            existing = f.read().strip()
        if existing and existing != pid:
            try:
                os.kill(int(existing), 0)
                log.warning(f'Instance already running with PID {existing}')
                return False
            except (OSError, ValueError):
                log.info('Stale PID file, will overwrite')
    with open(PID_FILE, 'w') as f:
        f.write(pid)
    return True


def remove_pid_file():
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


def signal_handler(signum, frame):
    sig_name = signal.Signals(signum).name
    log.info(f'Received {sig_name}, shutting down gracefully')
    remove_pid_file()
    sys.exit(0)


def ws_write(payload):
    import requests
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f'write_service error: {e}')
        return None


def ws_query(sql):
    import requests
    try:
        resp = requests.post(QUERY_SERVICE_URL, json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f'query_service error: {e}')
        return None


def ws_execute(sql):
    import requests
    try:
        resp = requests.post(EXECUTE_SERVICE_URL, json={'sql': sql}, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f'execute_service error: {e}')
        return None


def send_heartbeat():
    payload = {
        'table': 'service_health',
        'rows': {'service': SERVICE_NAME, 'last_heartbeat': time.time()}
    }
    ws_write(payload)
    log.debug('Heartbeat sent')


def ensure_tables():
    sql = """
    CREATE TABLE IF NOT EXISTS scanner_fingerprint_evidence (
        server_id VARCHAR,
        scan_url VARCHAR,
        mcp_methods_detected VARCHAR,
        is_mcp_traffic BOOLEAN,
        session_indicators VARCHAR,
        session_id VARCHAR,
        mcp_endpoint_indicator BOOLEAN,
        confidence_score DOUBLE,
        evidence_text VARCHAR,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (server_id, scan_url)
    )
    """
    ws_execute(sql)
    log.info('Tables ensured')


def analyze_http_response(server_id, scan_url, response_text, headers=None):
    if not response_text:
        return None
    
    headers = headers or {}
    
    mcp_methods = detect_mcp_methods(response_text, headers)
    is_mcp = is_mcp_traffic(response_text, headers)
    session_indicators = extract_session_indicators(response_text, headers)
    session_id = extract_session_id(response_text, headers)
    mcp_endpoint = has_mcp_endpoint_indicator(response_text, headers)
    
    confidence = 0.0
    evidence_parts = []
    
    if is_mcp:
        confidence += 0.4
        evidence_parts.append('MCP traffic patterns detected')
    
    if mcp_methods:
        method_count = len(mcp_methods)
        confidence += min(0.3, method_count * 0.05)
        evidence_parts.append(f'MCP methods found: {", ".join(mcp_methods[:10])}')
    
    if mcp_endpoint:
        confidence += 0.15
        evidence_parts.append('MCP endpoint indicators present')
    
    if session_indicators:
        confidence += 0.1
        evidence_parts.append(f'Session indicators: {len(session_indicators)} found')
    
    if session_id:
        confidence += 0.05
        evidence_parts.append('Session ID extracted')
    
    confidence = min(1.0, confidence)
    
    evidence_text = '; '.join(evidence_parts) if evidence_parts else 'No MCP fingerprints detected'
    
    result = {
        'server_id': server_id,
        'scan_url': scan_url,
        'mcp_methods_detected': ','.join(mcp_methods) if mcp_methods else '',
        'is_mcp_traffic': is_mcp,
        'session_indicators': ','.join(session_indicators) if session_indicators else '',
        'session_id': session_id or '',
        'mcp_endpoint_indicator': mcp_endpoint,
        'confidence_score': confidence,
        'evidence_text': evidence_text,
    }
    
    return result


def save_fingerprint_evidence(server_id, scan_url, response_text, headers=None):
    evidence = analyze_http_response(server_id, scan_url, response_text, headers)
    if not evidence:
        return False
    
    payload = {
        'table': 'scanner_fingerprint_evidence',
        'rows': evidence
    }
    result = ws_write(payload)
    if result and result.get('ok'):
        log.info(f'Fingerprint evidence saved for server {server_id} ({scan_url}): confidence={evidence["confidence_score"]:.2f}')
        return True
    else:
        log.error(f'Failed to save fingerprint evidence for {server_id}')
        return False


def get_unprocessed_candidates(limit=50):
    sql = f"""
    SELECT 
        server_id,
        url AS scan_url,
        scan_response,
        scan_headers
    FROM mcp_server_registry r
    LEFT JOIN scanner_fingerprint_evidence e ON r.server_id = e.server_id AND r.url = e.scan_url
    WHERE e.server_id IS NULL
    AND r.url IS NOT NULL
    LIMIT {limit}
    """
    result = ws_query(sql)
    if result and result.get('rows'):
        return result['rows']
    return []


def process_candidate(server_id, scan_url, scan_response, scan_headers=None):
    if not scan_response:
        return False
    
    try:
        if isinstance(scan_headers, str):
            import json
            scan_headers = json.loads(scan_headers)
    except (json.JSONDecodeError, TypeError):
        scan_headers = None
    
    return save_fingerprint_evidence(server_id, scan_url, scan_response, scan_headers)


def run_cycle():
    log.info('Starting fingerprint analysis cycle')
    candidates = get_unprocessed_candidates(limit=50)
    
    if not candidates:
        log.debug('No unprocessed candidates found')
        return 0
    
    log.info(f'Processing {len(candidates)} candidates')
    processed = 0
    
    for candidate in candidates:
        server_id = candidate.get('server_id')
        scan_url = candidate.get('scan_url')
        scan_response = candidate.get('scan_response', '')
        scan_headers = candidate.get('scan_headers')
        
        if not server_id or not scan_url:
            continue
        
        try:
            if process_candidate(server_id, scan_url, scan_response, scan_headers):
                processed += 1
        except Exception as e:
            log.error(f'Error processing {server_id}: {e}')
    
    log.info(f'Cycle complete: processed {processed}/{len(candidates)} candidates')
    return processed


def heartbeat_loop():
    last_heartbeat = 0
    while True:
        now = time.time()
        if now - last_heartbeat >= HEARTBEAT_INTERVAL:
            send_heartbeat()
            last_heartbeat = now
        time.sleep(5)


def run():
    import os
    import threading
    
    log.info(f'Starting {SERVICE_NAME} daemon on port {SERVICE_PORT}')
    
    if not check_single_instance():
        sys.exit(1)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    ensure_tables()
    send_heartbeat()
    
    heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    heartbeat_thread.start()
    
    log.info('Fingerprint wiring daemon running')
    
    while True:
        try:
            run_cycle()
        except Exception as e:
            log.error(f'Cycle error: {e}')
        
        time.sleep(POLL_SECS)


def analyze_response_for_scanner(server_id, scan_url, response_text, headers=None):
    return analyze_http_response(server_id, scan_url, response_text, headers)


def is_candidate_mcp_server(response_text, headers=None):
    return is_mcp_traffic(response_text, headers)


def get_mcp_method_signature(response_text, headers=None):
    return detect_mcp_methods(response_text, headers)


if __name__ == '__main__':
    run()