import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests

sys.path.insert(0, '/home/workspace/zo_sentinel')
sys.path.insert(0, '/home/workspace')

from mcp_traffic_fingerprints import (
    detect_mcp_methods,
    extract_session_indicators,
    is_mcp_traffic,
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler('/home/workspace/logs/mcp_scanner_fingerprints_wiring.log'),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger(__name__)

SERVICE_NAME = 'mcp_scanner_fingerprints_wiring'
SERVICE_PORT = 0
PID_FILE = '/tmp/mcp_scanner_fingerprints_wiring.pid'
WRITE_SERVICE_URL = 'http://localhost:8772'
EXECUTE_URL = 'http://localhost:8772/execute'
QUERY_URL = 'http://localhost:8772/query'
HEARTBEAT_INTERVAL = 300
POLL_SECS = 60


def ws_write(table: str, rows: List[Dict[str, Any]]) -> bool:
    payload = {'table': table, 'rows': rows, 'wait': True}
    try:
        resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
        if resp.status_code == 200:
            return True
        log.warning('ws_write failed for %s: %s %s', table, resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        log.error('ws_write exception for %s: %s', table, e)
        return False


def ws_query(sql: str) -> Optional[List[Dict[str, Any]]]:
    payload = {'sql': sql}
    try:
        resp = requests.post(QUERY_URL, json=payload, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            return data.get('rows', [])
        log.warning('ws_query failed: %s %s', resp.status_code, resp.text[:200])
        return None
    except Exception as e:
        log.error('ws_query exception: %s', e)
        return None


def ws_execute(sql: str) -> bool:
    payload = {'sql': sql}
    try:
        resp = requests.post(EXECUTE_URL, json=payload, timeout=30)
        if resp.status_code == 200:
            return True
        log.warning('ws_execute failed: %s %s', resp.status_code, resp.text[:200])
        return False
    except Exception as e:
        log.error('ws_execute exception: %s', e)
        return False


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')


def check_single_instance() -> bool:
    pid_file = PID_FILE
    if os.path.exists(pid_file):
        with open(pid_file, 'r') as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            log.error('Another instance is running with PID %s. Exiting.', old_pid)
            return False
        except (OSError, ValueError):
            log.info('Stale PID file found, removing it.')
            os.remove(pid_file)
    return True


def remove_pid_file() -> None:
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
    except Exception as e:
        log.error('Error removing PID file: %s', e)


def signal_handler(signum, frame) -> None:
    signame = 'SIGTERM' if signum == 15 else 'SIGINT' if signum == 2 else str(signum)
    log.info('Received %s, shutting down gracefully.', signame)
    remove_pid_file()
    sys.exit(0)


def send_heartbeat(meta: Optional[Dict[str, Any]] = None) -> None:
    row = {
        'service': SERVICE_NAME,
        'last_heartbeat': utc_now_iso(),
        'status': 'ok',
        'meta': meta or {},
    }
    ws_write('service_health', [row])


def ensure_mcp_fingerprints_table() -> bool:
    sql = """
    CREATE TABLE IF NOT EXISTS mcp_fingerprints (
        server_id VARCHAR,
        scan_id VARCHAR,
        fingerprint_hash VARCHAR,
        session_indicators JSON,
        mcp_methods JSON,
        is_mcp_confirmed BOOLEAN,
        computed_at TIMESTAMPTZ,
        PRIMARY KEY (server_id, scan_id, fingerprint_hash)
    )
    """
    return ws_execute(sql)


def compute_fingerprint_hash(server_id: str, content_sample: str) -> str:
    import hashlib
    combined = f'{server_id}:{content_sample[:1000]}'
    return hashlib.sha256(combined.encode()).hexdigest()[:32]


def analyze_response_for_mcp(
    content: str,
    headers: Optional[Dict[str, str]] = None,
    url: Optional[str] = None
) -> Dict[str, Any]:
    result = {
        'is_mcp_traffic': False,
        'detected_methods': [],
        'session_indicators': {},
        'confidence': 0.0,
    }
    
    if not content:
        return result
    
    result['is_mcp_traffic'] = is_mcp_traffic(content, headers)
    
    if result['is_mcp_traffic']:
        result['detected_methods'] = detect_mcp_methods(content)
        result['session_indicators'] = extract_session_indicators(content, url)
        result['confidence'] = 1.0 if result['detected_methods'] else 0.5
    
    return result


def write_fingerprint_evidence(
    server_id: str,
    scan_id: str,
    analysis_result: Dict[str, Any],
    content_sample: str
) -> bool:
    if not analysis_result.get('is_mcp_traffic'):
        return False
    
    fingerprint_hash = compute_fingerprint_hash(server_id, content_sample)
    
    import json
    row = {
        'server_id': server_id,
        'scan_id': scan_id,
        'fingerprint_hash': fingerprint_hash,
        'session_indicators': json.dumps(analysis_result.get('session_indicators', {})),
        'mcp_methods': json.dumps(analysis_result.get('detected_methods', [])),
        'is_mcp_confirmed': True,
        'computed_at': utc_now_iso(),
    }
    
    return ws_write('mcp_fingerprints', [row])


def get_pending_scans(limit: int = 50) -> List[Dict[str, Any]]:
    sql = f"""
    SELECT 
        s.server_id,
        s.scan_id,
        s.url,
        s.status,
        s.scanned_at
    FROM mcp_server_registry r
    JOIN (
        SELECT 
            server_id,
            server_id AS scan_id,
            url,
            'pending' AS status,
            last_scanned AS scanned_at,
            ROW_NUMBER() OVER (PARTITION BY server_id ORDER BY last_scanned DESC) as rn
        FROM mcp_server_registry
        WHERE url IS NOT NULL AND url != ''
    ) s ON r.server_id = s.server_id
    WHERE s.rn = 1
    AND NOT EXISTS (
        SELECT 1 FROM mcp_fingerprints fp 
        WHERE fp.server_id = s.server_id 
        AND fp.scan_id = s.scan_id
        AND fp.is_mcp_confirmed = TRUE
    )
    LIMIT {limit}
    """
    result = ws_query(sql)
    return result if result else []


def fetch_server_response(url: str, timeout: int = 15) -> tuple:
    headers = {
        'User-Agent': 'MCP-Scanner/1.0 (zo-sentinel)',
        'Accept': 'application/json, text/plain, */*',
    }
    
    try:
        resp = requests.get(url, headers=headers, timeout=timeout, allow_redirects=True)
        content = resp.text
        resp_headers = dict(resp.headers)
        status_code = resp.status_code
        return content, resp_headers, status_code, None
    except requests.exceptions.Timeout:
        return '', {}, 0, 'timeout'
    except requests.exceptions.ConnectionError:
        return '', {}, 0, 'connection_error'
    except Exception as e:
        return '', {}, 0, str(e)


def process_server_scan(server_id: str, url: str, scan_id: str) -> bool:
    log.info('Scanning %s at %s for MCP fingerprint', server_id, url)
    
    content, headers, status_code, error = fetch_server_response(url)
    
    if error:
        log.warning('Failed to fetch %s: %s', url, error)
        return False
    
    if status_code < 200 or status_code >= 400:
        log.warning('Non-success status for %s: %d', url, status_code)
        return False
    
    analysis = analyze_response_for_mcp(content, headers, url)
    
    if analysis['is_mcp_traffic']:
        log.info(
            'MCP confirmed for %s: methods=%s',
            server_id,
            analysis['detected_methods']
        )
        return write_fingerprint_evidence(server_id, scan_id, analysis, content)
    else:
        log.debug('No MCP traffic detected for %s', server_id)
        return False


def get_scanner_statistics() -> Dict[str, Any]:
    sql = """
    SELECT 
        COUNT(DISTINCT server_id) as servers_scanned,
        SUM(CASE WHEN is_mcp_confirmed THEN 1 ELSE 0 END) as mcp_confirmed,
        SUM(CASE WHEN NOT is_mcp_confirmed THEN 1 ELSE 0 END) as not_mcp,
        COUNT(*) as total_fingerprints
    FROM mcp_fingerprints
    """
    result = ws_query(sql)
    if result and len(result) > 0:
        return result[0]
    return {'servers_scanned': 0, 'mcp_confirmed': 0, 'not_mcp': 0, 'total_fingerprints': 0}


def cycle() -> int:
    stats = get_scanner_statistics()
    log.info('Scanner stats: %s', stats)
    
    pending = get_pending_scans(limit=100)
    log.info('Found %d pending servers to scan', len(pending))
    
    processed = 0
    for server in pending:
        server_id = server.get('server_id')
        url = server.get('url')
        scan_id = server.get('scan_id')
        
        if not server_id or not url:
            continue
        
        if process_server_scan(server_id, url, scan_id):
            processed += 1
        
        time.sleep(0.5)
    
    log.info('Cycle complete: processed %d servers with MCP confirmed', processed)
    return processed


def heartbeat_loop() -> None:
    while True:
        try:
            stats = get_scanner_statistics()
            send_heartbeat({'servers_scanned': stats.get('servers_scanned', 0),
                           'mcp_confirmed': stats.get('mcp_confirmed', 0)})
        except Exception as e:
            log.error('Heartbeat failed: %s', e)
        time.sleep(HEARTBEAT_INTERVAL)


def run() -> None:
    log.info('Starting %s', SERVICE_NAME)
    
    if not check_single_instance():
        sys.exit(1)
    
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))
    
    try:
        ensure_mcp_fingerprints_table()
        log.info('mcp_fingerprints table ensured')
    except Exception as e:
        log.error('Failed to ensure table: %s', e)
    
    send_heartbeat({'status': 'started'})
    
    import threading
    hb_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    hb_thread.start()
    
    while True:
        try:
            cycle()
        except Exception as e:
            log.error('Cycle error: %s', e)
        
        time.sleep(POLL_SECS)


if __name__ == '__main__':
    run()