import sys
import os
import logging
import time
import hashlib
import requests
from datetime import datetime, timezone

sys.path.insert(0, '/home/workspace/zo_sentinel')

import mcp_traffic_fingerprints as fingerprint_fns

SERVICE_NAME = 'fingerprint_scanner_wiring'
LOG_DIR = '/home/workspace/logs'
LOG_FILE = f'{LOG_DIR}/{SERVICE_NAME}.log'
LOG_DIR_PATH = os.path.dirname(LOG_FILE)
LOG = logging.getLogger(SERVICE_NAME)

WRITE_SERVICE_URL = 'http://127.0.0.1:8772'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772/query'
EXECUTE_SERVICE_URL = 'http://127.0.0.1:8772/execute'
WRITE_API_URL = f'{WRITE_SERVICE_URL}/write'
QUERY_API_URL = f'{QUERY_SERVICE_URL}'
EXECUTE_API_URL = f'{EXECUTE_SERVICE_URL}/execute'

PID_FILE = f'/tmp/{SERVICE_NAME}.pid'
POLL_SECS = 300
PORT = None

def ws_write(table, rows):
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_API_URL, json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()

def ws_query(sql):
    payload = {'sql': sql}
    resp = requests.post(QUERY_API_URL, json=payload, timeout=30)
    resp.raise_for_status()
    result = resp.json()
    return result.get('rows', [])

def ws_execute(sql):
    payload = {'sql': sql}
    resp = requests.post(EXECUTE_API_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def check_single_instance():
    pid = str(os.getpid())
    if os.path.exists(PID_FILE):
        old = open(PID_FILE).read().strip()
        if old and old != pid:
            try:
                os.kill(int(old), 0)
                LOG.error('Already running with PID %s', old)
                sys.exit(1)
            except OSError:
                LOG.warning('Stale PID file from %s, taking over', old)
        else:
            LOG.warning('Re-acquiring PID file %s', PID_FILE)
    with open(PID_FILE, 'w') as f:
        f.write(pid)
    LOG.info('Acquired PID file: %s', PID_FILE)

def remove_pid_file():
    try:
        if os.path.exists(PID_FILE):
            os.remove(PID_FILE)
            LOG.info('Released PID file: %s', PID_FILE)
    except Exception as e:
        LOG.warning('Failed to remove PID file: %s', e)

def signal_handler(signum, frame):
    LOG.info('Received signal %d, shutting down', signum)
    remove_pid_file()
    sys.exit(0)

def send_heartbeat(status='running', meta=None):
    now = datetime.now(timezone.utc).isoformat()
    rows = [{'service': SERVICE_NAME, 'status': status, 'ts': now, 'meta': meta or {}}]
    try:
        ws_write('service_health', rows)
    except Exception as e:
        LOG.warning('Heartbeat failed: %s', e)

def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()

def get_pending_scan_candidates(limit=50):
    sql = f"""
    SELECT server_id, name, url, description, scan_count
    FROM mcp_server_registry
    WHERE scan_count >= 0
    ORDER BY scan_count ASC, server_id
    LIMIT {limit}
    """
    try:
        return ws_query(sql)
    except Exception as e:
        LOG.error('Failed to query pending candidates: %s', e)
        return []

def fetch_server_response(url, timeout=10):
    try:
        resp = requests.get(url, timeout=timeout, headers={'Accept': 'application/json'})
        if resp.status_code in (200, 401, 403):
            return resp.text[:8192]
    except Exception as e:
        LOG.debug('Fetch failed for %s: %s', url, e)
    return None

def confirm_mcp_protocol(server_id, url, response_body):
    if response_body is None:
        return False
    confirmed = fingerprint_fns.is_mcp_traffic(response_body)
    if confirmed:
        methods = fingerprint_fns.detect_mcp_methods(response_body)
        session_indicators = fingerprint_fns.extract_session_indicators(response_body)
        LOG.info('MCP confirmed for %s (%s) methods=%s session=%s',
                 server_id, url, methods, session_indicators)
    else:
        LOG.debug('MCP not confirmed for %s (%s)', server_id, url)
    return confirmed

def enrich_registry_with_confirmation(server_id, url, response_body, scan_count):
    confirmed = confirm_mcp_protocol(server_id, url, response_body)
    confirmed_flag = confirmed if confirmed else None
    new_scan_count = scan_count + 1
    confirmed_val = 'true' if confirmed else 'false'
    sql = f"""
    UPDATE mcp_server_registry
    SET scan_count = {new_scan_count},
        confirmed_mcp = {confirmed_val}
    WHERE server_id = '{server_id}'
    """
    try:
        ws_execute(sql)
    except Exception as e:
        LOG.error('Failed to update confirmation for %s: %s', server_id, e)

def ensure_confirmed_mcp_column():
    sql_check = """
    SELECT column_name FROM information_schema.columns
    WHERE table_name = 'mcp_server_registry'
    AND column_name = 'confirmed_mcp'
    """
    try:
        rows = ws_query(sql_check)
        if not rows:
            sql_alt = "ALTER TABLE mcp_server_registry ADD COLUMN confirmed_mcp BOOLEAN"
            ws_execute(sql_alt)
            LOG.info('Added confirmed_mcp column to mcp_server_registry')
        else:
            LOG.debug('confirmed_mcp column already exists')
    except Exception as e:
        LOG.warning('Schema check for confirmed_mcp failed: %s', e)

def cycle():
    ensure_confirmed_mcp_column()
    candidates = get_pending_scan_candidates(limit=50)
    if not candidates:
        LOG.info('No pending candidates to process')
        return
    processed = 0
    for row in candidates:
        server_id = row.get('server_id', '')
        url = row.get('url', '') or ''
        scan_count = int(row.get('scan_count', 0) or 0)
        if not url:
            continue
        response_body = fetch_server_response(url)
        if response_body is not None:
            enrich_registry_with_confirmation(server_id, url, response_body, scan_count)
        else:
            ws_execute(f"UPDATE mcp_server_registry SET scan_count = {scan_count + 1} WHERE server_id = '{server_id}'")
            LOG.debug('Fetch failed, incremented scan_count for %s', server_id)
        processed += 1
        time.sleep(0.5)
    LOG.info('Cycle complete: processed %d candidates', processed)

def run():
    LOG.info('Starting %s', SERVICE_NAME)
    check_single_instance()
    import signal
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    while True:
        try:
            cycle()
        except Exception as e:
            LOG.error('Cycle failed: %s', e)
        send_heartbeat(status='running', meta={'last_cycle': utc_now_iso()})
        time.sleep(POLL_SECS)

if __name__ == '__main__':
    if LOG_DIR_PATH and not os.path.exists(LOG_DIR_PATH):
        os.makedirs(LOG_DIR_PATH, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
        handlers=[logging.FileHandler(LOG_FILE, encoding='utf-8')]
    )
    run()