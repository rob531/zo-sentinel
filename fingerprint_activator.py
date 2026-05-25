import sys
sys.path.insert(0, '/home/workspace/zo_sentinel')

import os
import time
import logging
import requests
import threading
from datetime import datetime, timezone

WRITE_SERVICE_URL = 'http://127.0.0.1:8772/write'
QUERY_SERVICE_URL = 'http://127.0.0.1:8772/query'
SERVICE_NAME = 'fingerprint_activator'
PID_FILE = '/tmp/fingerprint_activator.pid'
LOCK_FILE = '/home/workspace/logs/fingerprint_activator.lock'
LOG_FILE = '/home/workspace/logs/fingerprint_activator.log'
HEARTBEAT_INTERVAL = 30
POLL_SECS = 600
BATCH_SIZE = 50

_start_time = None
_last_heartbeat = None
_heartbeat_thread = None
_stop_event = threading.Event()


def setup_logging():
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    logger = logging.getLogger(SERVICE_NAME)
    logger.setLevel(logging.INFO)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    fh = logging.FileHandler(LOG_FILE)
    fh.setLevel(logging.INFO)
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    fh.setFormatter(formatter)
    logger.addHandler(fh)
    return logger


def ws_query(sql):
    resp = requests.post(QUERY_SERVICE_URL, json={'sql': sql}, timeout=30)
    resp.raise_for_status()
    return resp.json()


def ws_write(table, rows):
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()


def check_single_instance():
    pid = os.getpid()
    if os.path.exists(LOCK_FILE):
        with open(LOCK_FILE, 'r') as f:
            existing_pid = int(f.read().strip())
        try:
            os.kill(existing_pid, 0)
            return False
        except OSError:
            pass
    with open(LOCK_FILE, 'w') as f:
        f.write(str(pid))
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = int(f.read().strip())
            try:
                os.kill(old_pid, 0)
                return False
            except OSError:
                pass
    with open(PID_FILE, 'w') as f:
        f.write(str(pid))
    return True


def send_heartbeat():
    global _last_heartbeat
    try:
        ws_write('service_health', {'service': SERVICE_NAME, 'last_heartbeat': datetime.now(timezone.utc).isoformat()})
        _last_heartbeat = time.time()
    except Exception:
        pass


def heartbeat_loop():
    while not _stop_event.is_set():
        send_heartbeat()
        for _ in range(HEARTBEAT_INTERVAL):
            if _stop_event.is_set():
                break
            time.sleep(1)


def get_servers_without_fingerprints(limit):
    sql = f"""
        SELECT server_id 
        FROM mcp_server_registry 
        WHERE server_id NOT IN (
            SELECT server_id FROM mcp_fingerprints WHERE server_id IS NOT NULL
        ) 
        LIMIT {limit}
    """
    result = ws_query(sql)
    return [row['server_id'] for row in result.get('rows', [])]


def import_fingerprinter():
    import mcp_fingerprinter
    return mcp_fingerprinter


def run():
    global _start_time, _heartbeat_thread
    logger = setup_logging()
    
    if not check_single_instance():
        logger.error('Another instance is running. Exiting.')
        return
    
    logger.info(f'{SERVICE_NAME} starting')
    _start_time = time.time()
    
    _heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
    _heartbeat_thread.start()
    
    try:
        import mcp_fingerprinter
        logger.info('mcp_fingerprinter module loaded successfully')
    except ImportError as e:
        logger.error(f'Failed to import mcp_fingerprinter: {e}')
        return
    
    while True:
        batch_start = time.time()
        logger.info('Starting fingerprint batch')
        
        fingerprinted_count = 0
        none_returned_count = 0
        exception_count = 0
        
        try:
            server_ids = get_servers_without_fingerprints(BATCH_SIZE)
            logger.info(f'Found {len(server_ids)} servers without fingerprints')
        except Exception as e:
            logger.error(f'Failed to query servers: {e}')
            time.sleep(POLL_SECS)
            continue
        
        for server_id in server_ids:
            try:
                result = mcp_fingerprinter.generate_fingerprint(server_id)
                if result is None:
                    none_returned_count += 1
                    logger.info(f'generate_fingerprint returned None for server_id={server_id}')
                else:
                    fingerprinted_count += 1
                    logger.info(f'fingerprinted server_id={server_id}')
            except Exception as e:
                exception_count += 1
                logger.warning(f'Exception for server_id={server_id}: {e}')
                continue
        
        batch_duration = time.time() - batch_start
        logger.info(
            f'Batch complete: fingerprinted_count={fingerprinted_count}, '
            f'none_returned_count={none_returned_count}, '
            f'exception_count={exception_count}, '
            f'batch_duration_s={batch_duration:.2f}'
        )
        
        time.sleep(POLL_SECS)


def get_uptime_seconds():
    if _start_time is None:
        return 0
    return int(time.time() - _start_time)


def health():
    return {
        'status': 'ok',
        'service': SERVICE_NAME,
        'uptime': get_uptime_seconds(),
        'last_heartbeat': _last_heartbeat
    }


if __name__ == '__main__':
    run()