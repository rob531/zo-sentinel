import os
import sys
import time
import json
import logging
import signal
import hashlib
import requests
from datetime import datetime, timezone
from functools import lru_cache

SERVICE_NAME = 'email_guid_auth_compact_v2'
SERVICE_PORT = None
WRITE_SERVICE_URL = 'http://localhost:8772'
PID_FILE = f'/home/workspace/zo_sentinel/{SERVICE_NAME}.pid'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.FileHandler(f'/home/workspace/logs/{SERVICE_NAME}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

_process_alive = True

def signal_handler(signum, frame):
    global _process_alive
    logger.info(f"Received signal {signum}, initiating shutdown")
    _process_alive = False

def check_single_instance():
    if os.path.exists(PID_FILE):
        with open(PID_FILE, 'r') as f:
            old_pid = f.read().strip()
        try:
            os.kill(int(old_pid), 0)
            logger.error(f"Service already running with PID {old_pid}")
            sys.exit(1)
        except (OSError, ValueError):
            pass
    with open(PID_FILE, 'w') as f:
        f.write(str(os.getpid()))

def remove_pid_file():
    if os.path.exists(PID_FILE):
        os.remove(PID_FILE)

def ws_write(table, rows):
    payload = {'table': table, 'rows': rows, 'wait': True}
    resp = requests.post(WRITE_SERVICE_URL, json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def ws_query(sql, params=None):
    payload = {'sql': sql, 'params': params} if params else {'sql': sql}
    resp = requests.post(WRITE_SERVICE_URL + '/query', json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()

def send_heartbeat(status='running', meta=None):
    row = {
        'service_name': SERVICE_NAME,
        'status': status,
        'ts': datetime.now(timezone.utc).isoformat(),
        'meta': json.dumps(meta) if meta else '{}'
    }
    ws_write('service_health', row)

@lru_cache(maxsize=1024)
def _hash_guid(guid):
    return hashlib.sha256(guid.encode()).hexdigest()[:32]

def validate_guid_format(guid):
    if not guid or not isinstance(guid, str):
        return False
    parts = guid.split('-')
    if len(parts) != 5:
        return False
    return True

def lookup_auth_by_guid(guid):
    if not validate_guid_format(guid):
        return None
    hashed = _hash_guid(guid)
    sql = "SELECT email_address, auth_level, created_at FROM email_guid_auth WHERE guid_hash = ? LIMIT 1"
    try:
        result = ws_query(sql, [hashed])
        if result.get('rows'):
            return result['rows'][0]
    except Exception as e:
        logger.warning(f"GUID lookup failed: {e}")
    return None

def create_guid_mapping(email, guid, auth_level='standard'):
    if not validate_guid_format(guid):
        return False
    hashed = _hash_guid(guid)
    ts = datetime.now(timezone.utc).isoformat()
    row = {
        'guid_hash': hashed,
        'email_address': email,
        'auth_level': auth_level,
        'created_at': ts
    }
    try:
        ws_write('email_guid_auth', row)
        return True
    except Exception as e:
        logger.error(f"Failed to create GUID mapping: {e}")
        return False

def revoke_guid(guid):
    if not validate_guid_format(guid):
        return False
    hashed = _hash_guid(guid)
    sql = "UPDATE email_guid_auth SET revoked = 1, revoked_at = ? WHERE guid_hash = ? AND revoked = 0"
    try:
        requests.post(WRITE_SERVICE_URL + '/execute', 
                      json={'sql': sql, 'params': [datetime.now(timezone.utc).isoformat(), hashed]},
                      timeout=30)
        return True
    except Exception as e:
        logger.error(f"Failed to revoke GUID: {e}")
        return False
    return False

def verify_email_guid_pair(email, guid):
    result = lookup_auth_by_guid(guid)
    if not result:
        return False
    return result.get('email_address') == email

POLL_SECS = 60

def cycle():
    try:
        sql = "SELECT COUNT(*) as cnt FROM email_guid_auth"
        result = ws_query(sql)
        count = result.get('rows', [{}])[0].get('cnt', 0)
        return {'mappings_count': count, 'status': 'ok'}
    except Exception as e:
        logger.error(f"Cycle error: {e}")
        return {'status': 'error', 'error': str(e)}

def run():
    global _process_alive
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    check_single_instance()
    logger.info(f"{SERVICE_NAME} starting")
    send_heartbeat('started')
    try:
        while _process_alive:
            result = cycle()
            send_heartbeat('running', result)
            time.sleep(POLL_SECS)
    finally:
        send_heartbeat('stopped')
        remove_pid_file()
        logger.info(f"{SERVICE_NAME} stopped")

if __name__ == '__main__':
    run()